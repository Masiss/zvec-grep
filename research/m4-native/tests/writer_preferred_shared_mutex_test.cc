#include "writer_preferred_shared_mutex.h"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <iostream>
#include <mutex>
#include <thread>

namespace {

using Clock = std::chrono::steady_clock;

template <typename Predicate>
bool WaitUntil(const Predicate& predicate) {
  const auto deadline = Clock::now() + std::chrono::seconds(3);
  while (!predicate()) {
    if (Clock::now() >= deadline) return false;
    std::this_thread::yield();
  }
  return true;
}

bool TestConcurrentReaders() {
  m4::detail::WriterPreferredSharedMutex mutex;
  mutex.lock_shared();
  std::atomic<bool> concurrent_reader_admitted{false};
  std::thread reader([&] {
    if (mutex.try_lock_shared()) {
      concurrent_reader_admitted.store(true, std::memory_order_release);
      mutex.unlock_shared();
    }
  });
  reader.join();
  mutex.unlock_shared();
  if (!concurrent_reader_admitted.load(std::memory_order_acquire)) return false;

  if (!mutex.try_lock()) return false;
  mutex.unlock();
  return true;
}

bool TestWaitingWriterPrecedesLaterReader() {
  m4::detail::WriterPreferredSharedMutex mutex;
  mutex.lock_shared();

  std::mutex release_mutex;
  std::condition_variable release_condition;
  bool release_writer = false;
  std::atomic<bool> writer_acquired{false};
  std::atomic<bool> later_reader_acquired{false};

  std::thread writer([&] {
    mutex.lock();
    writer_acquired.store(true, std::memory_order_release);
    std::unique_lock<std::mutex> guard(release_mutex);
    release_condition.wait(guard, [&] { return release_writer; });
    mutex.unlock();
  });

  const bool writer_queued =
      WaitUntil([&] { return mutex.WaitingWritersForTesting() == 1; });
  std::thread later_reader([&] {
    mutex.lock_shared();
    later_reader_acquired.store(true, std::memory_order_release);
    mutex.unlock_shared();
  });
  const bool reader_queued =
      WaitUntil([&] { return mutex.WaitingReadersForTesting() == 1; });

  // Releasing the initial reader must admit the writer first. The writer is
  // held until the test confirms the later reader has not bypassed it.
  mutex.unlock_shared();
  const bool writer_won = WaitUntil(
      [&] { return writer_acquired.load(std::memory_order_acquire); });
  const bool reader_blocked =
      !later_reader_acquired.load(std::memory_order_acquire);

  {
    std::lock_guard<std::mutex> guard(release_mutex);
    release_writer = true;
  }
  release_condition.notify_one();

  const bool reader_eventually_admitted = WaitUntil(
      [&] { return later_reader_acquired.load(std::memory_order_acquire); });
  writer.join();
  later_reader.join();

  return writer_queued && reader_queued && writer_won && reader_blocked &&
         reader_eventually_admitted;
}

}  // namespace

int main() {
  if (!TestConcurrentReaders()) {
    std::cerr << "FAIL: concurrent readers were not admitted\n";
    return 1;
  }
  if (!TestWaitingWriterPrecedesLaterReader()) {
    std::cerr << "FAIL: waiting writer did not precede a later reader\n";
    return 1;
  }
  std::cout << "PASS: concurrent readers and writer preference\n";
  return 0;
}
