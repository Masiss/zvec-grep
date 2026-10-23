// Shared small utilities for the M4 research prototype.

#pragma once

#include <cstdint>
#include <cstring>
#include <string>
#include <string_view>

namespace m4 {

inline uint64_t Fnv1aInit() { return 1469598103934665603ull; }

inline void Fnv1aUpdate(uint64_t* h, const void* data, size_t size) {
  const auto* p = static_cast<const uint8_t*>(data);
  for (size_t i = 0; i < size; ++i) {
    *h ^= p[i];
    *h *= 1099511628211ull;
  }
}

inline uint64_t Fnv1a(const void* data, size_t size) {
  const auto* p = static_cast<const uint8_t*>(data);
  uint64_t h = 1469598103934665603ull;
  for (size_t i = 0; i < size; ++i) {
    h ^= p[i];
    h *= 1099511628211ull;
  }
  return h;
}

// Small self-contained SHA-256 for deterministic research metadata. Keeping it
// local avoids a runtime crypto-library dependency for the standalone oracle.
// Full blocks are consumed directly from input; only the at-most-128-byte
// padding tail is materialized, avoiding input-sized scratch copies.
inline std::string Sha256(std::string_view input) {
  static constexpr uint32_t k[64] = {
      0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
      0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
      0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
      0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
      0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
      0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
      0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
      0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
  auto rotr = [](uint32_t x, unsigned n) { return (x >> n) | (x << (32 - n)); };
  uint32_t h[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                   0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
  auto compress = [&](const uint8_t* block) {
    uint32_t w[64];
    for (unsigned i = 0; i < 16; ++i) {
      const auto* p = block + i * 4;
      w[i] = (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) |
             (uint32_t(p[2]) << 8) | uint32_t(p[3]);
    }
    for (unsigned i = 16; i < 64; ++i) {
      const uint32_t s0 = rotr(w[i-15],7) ^ rotr(w[i-15],18) ^ (w[i-15] >> 3);
      const uint32_t s1 = rotr(w[i-2],17) ^ rotr(w[i-2],19) ^ (w[i-2] >> 10);
      w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    uint32_t a=h[0], b=h[1], c=h[2], d=h[3], e=h[4], f=h[5], g=h[6], z=h[7];
    for (unsigned i = 0; i < 64; ++i) {
      const uint32_t s1=rotr(e,6)^rotr(e,11)^rotr(e,25), ch=(e&f)^(~e&g);
      const uint32_t t1=z+s1+ch+k[i]+w[i];
      const uint32_t s0=rotr(a,2)^rotr(a,13)^rotr(a,22), maj=(a&b)^(a&c)^(b&c);
      const uint32_t t2=s0+maj;
      z=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    h[0]+=a; h[1]+=b; h[2]+=c; h[3]+=d;
    h[4]+=e; h[5]+=f; h[6]+=g; h[7]+=z;
  };
  const auto* bytes = reinterpret_cast<const uint8_t*>(input.data());
  const size_t full_blocks = input.size() / 64;
  for (size_t block = 0; block < full_blocks; ++block)
    compress(bytes + block * 64);

  uint8_t tail[128]{};
  const size_t remainder = input.size() % 64;
  if (remainder != 0) {
    std::memcpy(tail, bytes + full_blocks * 64, remainder);
  }
  tail[remainder] = 0x80;
  const size_t tail_blocks = remainder < 56 ? 1 : 2;
  const size_t length_offset = tail_blocks * 64 - 8;
  const uint64_t bit_len = static_cast<uint64_t>(input.size()) * 8;
  for (unsigned i = 0; i < 8; ++i)
    tail[length_offset + 7 - i] = static_cast<uint8_t>(bit_len >> (i * 8));
  for (size_t block = 0; block < tail_blocks; ++block)
    compress(tail + block * 64);
  static constexpr char hex[] = "0123456789abcdef";
  std::string out; out.reserve(64);
  for (uint32_t v : h) for (int shift = 28; shift >= 0; shift -= 4)
    out.push_back(hex[(v >> shift) & 0xf]);
  return out;
}

class BufWriter {
 public:
  void U8(uint8_t v) { buf_.push_back(static_cast<char>(v)); }
  void U32(uint32_t v) {
    char tmp[4];
    std::memcpy(tmp, &v, 4);
    buf_.append(tmp, 4);
  }
  void U64(uint64_t v) {
    char tmp[8];
    std::memcpy(tmp, &v, 8);
    buf_.append(tmp, 8);
  }
  void I64(int64_t v) { U64(static_cast<uint64_t>(v)); }
  void Bytes(const void* p, size_t n) {
    buf_.append(static_cast<const char*>(p), n);
  }
  void String(std::string_view s) {
    Varint(s.size());
    buf_.append(s.data(), s.size());
  }
  void Varint(uint64_t v) {
    while (v >= 0x80) {
      buf_.push_back(static_cast<char>((v & 0x7f) | 0x80));
      v >>= 7;
    }
    buf_.push_back(static_cast<char>(v));
  }
  void VarintSigned(int64_t v) {
    uint64_t z = (static_cast<uint64_t>(v) << 1) ^
                 static_cast<uint64_t>(v >> 63);
    Varint(z);
  }

  std::string& data() { return buf_; }
  const std::string& data() const { return buf_; }
  size_t size() const { return buf_.size(); }
  void Clear() { buf_.clear(); }

 private:
  std::string buf_;
};

class BufReader {
 public:
  BufReader(const char* data, size_t size)
      : begin_(data), p_(data), end_(data + size) {}

  size_t offset() const { return static_cast<size_t>(p_ - begin_); }

  bool ok() const { return ok_; }
  bool eof() const { return p_ >= end_; }
  size_t remaining() const { return static_cast<size_t>(end_ - p_); }

  uint8_t U8() {
    if (!Need(1)) return 0;
    return static_cast<uint8_t>(*p_++);
  }
  uint32_t U32() {
    if (!Need(4)) return 0;
    uint32_t v = 0;
    std::memcpy(&v, p_, 4);
    p_ += 4;
    return v;
  }
  uint64_t U64() {
    if (!Need(8)) return 0;
    uint64_t v = 0;
    std::memcpy(&v, p_, 8);
    p_ += 8;
    return v;
  }
  int64_t I64() { return static_cast<int64_t>(U64()); }
  uint64_t Varint() {
    uint64_t v = 0;
    int shift = 0;
    while (true) {
      if (!Need(1)) return 0;
      const uint8_t b = static_cast<uint8_t>(*p_++);
      v |= static_cast<uint64_t>(b & 0x7f) << shift;
      if ((b & 0x80) == 0) break;
      shift += 7;
      if (shift > 63) {
        ok_ = false;
        return 0;
      }
    }
    return v;
  }
  int64_t VarintSigned() {
    const uint64_t z = Varint();
    return static_cast<int64_t>((z >> 1) ^ (~(z & 1) + 1));
  }
  std::string String() {
    const uint64_t n = Varint();
    if (!ok_ || n > remaining()) {
      ok_ = false;
      return {};
    }
    std::string s(p_, static_cast<size_t>(n));
    p_ += n;
    return s;
  }
  std::string_view StringView() {
    const uint64_t n = Varint();
    if (!ok_ || n > remaining()) {
      ok_ = false;
      return {};
    }
    std::string_view s(p_, static_cast<size_t>(n));
    p_ += n;
    return s;
  }
  const char* raw(size_t n) {
    if (!Need(n)) return nullptr;
    const char* p = p_;
    p_ += n;
    return p;
  }
  bool Skip(size_t n) {
    if (!Need(n)) return false;
    p_ += n;
    return true;
  }
  void Fail() { ok_ = false; }

 private:
  bool Need(size_t n) {
    if (!ok_ || static_cast<size_t>(end_ - p_) < n) {
      ok_ = false;
      return false;
    }
    return true;
  }

  const char* begin_{nullptr};
  const char* p_{nullptr};
  const char* end_{nullptr};
  bool ok_{true};
};

}  // namespace m4
