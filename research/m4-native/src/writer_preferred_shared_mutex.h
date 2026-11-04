// Internal writer-preferred reader/writer lock for M4 query admission.
//
// Once a writer is waiting, new readers queue behind it. This preserves
// concurrent searches while preventing a continuous reader stream from
// starving a writer. Under a continuous writer stream, readers may wait.
#pragma once

#include <condition_variable>
#include <cstddef>
#include <exception>
#include <mutex>

namespace m4::detail {

class WriterPreferredSharedMutex {
 public:
  WriterPreferredSharedMutex() = default;
  WriterPreferredSharedMutex(const WriterPreferredSharedMutex&) = delete;
  WriterPreferredSharedMutex& operator=(const WriterPreferredSharedMutex&) = delete;

  void lock() {
    std::unique_lock<std::mutex> guard(mutex_);
    ++waiting_writers_;
    condition_.wait(guard, [this] {
      return !writer_active_ && active_readers_ == 0;
    });
    --waiting_writers_;
    writer_active_ = true;
  }

  void lock_shared() {
    std::unique_lock<std::mutex> guard(mutex_);
    ++waiting_readers_;
    condition_.wait(guard, [this] {
      return !writer_active_ && waiting_writers_ == 0;
    });
    --waiting_readers_;
    ++active_readers_;
  }

  bool try_lock() {
    std::lock_guard<std::mutex> guard(mutex_);
    if (writer_active_ || active_readers_ != 0 || waiting_writers_ != 0 ||
        waiting_readers_ != 0) {
      return false;
    }
    writer_active_ = true;
    return true;
  }

  bool try_lock_shared() {
    std::lock_guard<std::mutex> guard(mutex_);
    if (writer_active_ || waiting_writers_ != 0) return false;
    ++active_readers_;
    return true;
  }

  void unlock() {
    std::lock_guard<std::mutex> guard(mutex_);
    if (!writer_active_) std::terminate();
    writer_active_ = false;
    condition_.notify_all();
  }

  void unlock_shared() {
    std::lock_guard<std::mutex> guard(mutex_);
    if (active_readers_ == 0) std::terminate();
    --active_readers_;
    if (active_readers_ == 0) condition_.notify_all();
  }

  // Internal test observability, not part of the public M4 API.
  std::size_t WaitingWritersForTesting() const {
    std::lock_guard<std::mutex> guard(mutex_);
    return waiting_writers_;
  }

  std::size_t WaitingReadersForTesting() const {
    std::lock_guard<std::mutex> guard(mutex_);
    return waiting_readers_;
  }

 private:
  mutable std::mutex mutex_;
  std::condition_variable condition_;
  std::size_t active_readers_{0};
  std::size_t waiting_readers_{0};
  std::size_t waiting_writers_{0};
  bool writer_active_{false};
};

}  // namespace m4::detail
