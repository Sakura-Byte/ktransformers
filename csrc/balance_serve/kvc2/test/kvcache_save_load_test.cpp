#include "kvcache_test_utils.cpp"

int main(int argc, char* argv[]) {
  parse_and_check(argc, argv);
  spdlog::set_level(spdlog::level::debug);
  std::mt19937 gen(123);
  std::vector<KVCacheHandle> handles(10);

  {
    KVC2 kvc2(FLAGS_disk_cache_path);
    auto io = kvc2.io_dealer->start_io_thread();
    SPDLOG_WARN("Insert 10 x 10 KVCache");
    for (int i = 0; i < 10; i++) {
      handles[i] = random_kvcache(qwen_cache_info, 10, gen);
      auto& h1 = handles[i];
      h1.ids = random_ids(10 * BlockLength, gen);
      kvc2.raw_insert(h1);
    }

    kvc2.save();
    kvc2.tree->debug();

    kvc2.io_dealer->stop();
    io.join();
  }
  {
    KVC2 kvc2(FLAGS_disk_cache_path);
    auto io = kvc2.io_dealer->start_io_thread();
    kvc2.load();
    kvc2.tree->debug();
    auto& h1 = handles[0];
    // complete same
    {
      auto h2 = empty_kvcache(qwen_cache_info, 10);
      h2.ids = h1.ids;
      kvc2.raw_read(h2);
      assert(static_cast<size_t>(h2.match.match_length) == h1.ids.size());

      cmp_handle_data(h1, h2);
    }

    // complete prefix
    {
      auto h2 = empty_kvcache(qwen_cache_info, 10);

      h2.ids = std::vector<ID>(h1.ids.begin(), h1.ids.begin() + 3 * BlockLength);
      kvc2.raw_read(h2);
      assert(h2.match.match_length == 3 * BlockLength);

      cmp_handle_data(h1, h2, 3);
    }

    // common prefix
    {
      auto h2 = empty_kvcache(qwen_cache_info, 10);

      h2.ids = std::vector<ID>(h1.ids.begin(), h1.ids.begin() + 5 * BlockLength);
      auto rids = random_ids(BlockLength * 2 + BlockLength / 2, gen);
      h2.ids.insert(h2.ids.end(), rids.begin(), rids.end());

      kvc2.raw_read(h2);
      assert(h2.match.match_length == 5 * BlockLength);

      cmp_handle_data(h1, h2, 5);
    }

    // no prefix
    {
      auto h2 = empty_kvcache(qwen_cache_info, 10);

      h2.ids = random_ids(10 * BlockLength, gen);

      kvc2.raw_read(h2);
      assert(h2.match.match_length == 0);
    }

    // insert partly new
    auto h2 = random_kvcache(qwen_cache_info, 10, gen);
    copy_kvcache(h1, h2, 0, 5);
    h2.ids = random_ids(10 * BlockLength, gen);
    for (size_t i = 0; i < 5 * BlockLength; i++) {
      h2.ids[i] = h1.ids[i];
    }
    kvc2.raw_insert(h2);

    // read new part
    {
      auto h = empty_kvcache(qwen_cache_info, 10);
      h.ids = std::vector<ID>(h2.ids.begin(), h2.ids.begin() + 7 * BlockLength);
      h.ids.push_back(123);

      kvc2.raw_read(h);
      assert(h.match.match_length == 7 * BlockLength);
      cmp_handle_data(h, h2, 7);
    }

    kvc2.io_dealer->stop();
    io.join();
  }
  SPDLOG_WARN("{} Test Passed", __FILE__);
  return 0;
}