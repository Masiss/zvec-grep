// Internal test-only access to Index admission primitives.
#pragma once

#include "m4/index.h"
#include "writer_preferred_shared_mutex.h"

namespace m4::detail {

struct IndexTestAccess {
  static WriterPreferredSharedMutex &QueryMutex(Index &index);
};

}  // namespace m4::detail
