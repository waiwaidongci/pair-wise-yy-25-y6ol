import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import CorpusDB, DomainError


class ClaimQueueTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = CorpusDB(self.path)
        self.anns = [self.db.add_user(name, "annotator") for name in ("甲", "乙", "丙", "丁")]
        self.g = self.db.add_guideline("v1", "独立标注")
        self.batch = self.db.create_batch("测试批次", self.g)

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def make_item(self, required=2):
        ordinal = 1 + int(self.db.conn.execute("SELECT COUNT(*) FROM items WHERE batch_id=?", (self.batch,)).fetchone()[0])
        return self.db.add_item(self.batch, ordinal, f"文本{ordinal}", required)

    def test_item_slots_fill_then_queue_and_fifo_promotion_on_release(self):
        item = self.make_item(2)
        self.assertEqual("assigned", self.db.assign(item, self.anns[0])["state"])
        self.assertEqual("assigned", self.db.assign(item, self.anns[1])["state"])
        # 名额满，后续领取停在队列
        third = self.db.assign(item, self.anns[2])
        self.assertTrue(third["queued"])
        self.assertEqual(1, third["queue_position"])
        fourth = self.db.assign(item, self.anns[3])
        self.assertEqual(2, fourth["queue_position"])
        # 重复领取幂等：不重新排队
        again = self.db.assign(item, self.anns[2])
        self.assertTrue(again["queued"])
        self.assertEqual(2, len(self.db.snapshot()["claim_queue"]))
        # 交回未提交任务：立即放出名额，FIFO 补第一人
        result = self.db.release(item, self.anns[0])
        self.assertTrue(result["released"])
        self.assertEqual([{"item_id": item, "annotator_id": self.anns[2]}], result["promoted"])
        coverage = {c["item_id"]: c for c in self.db.item_coverage(self.batch)}[item]
        self.assertEqual(2, len(coverage["holding"]))
        self.assertIn("丙", coverage["holding"])
        self.assertEqual(["丁"], coverage["queued"])
        self.assertEqual(0, coverage["open_slots"])  # 仍占满 2 人（乙、丙）
        # 再交回，第二位排队者补入
        self.db.release(item, self.anns[1])
        coverage = {c["item_id"]: c for c in self.db.item_coverage(self.batch)}[item]
        self.assertEqual([], coverage["queued"])

    def test_personal_pending_cap_stops_grabbing_and_releases_after_submit(self):
        # 每个标注员未提交任务最多 2 份
        i1, i2, i3 = self.make_item(3), self.make_item(3), self.make_item(3)
        a = self.anns[0]
        self.assertEqual("assigned", self.db.assign(i1, a)["state"])
        self.assertEqual("assigned", self.db.assign(i2, a)["state"])
        third = self.db.assign(i3, a)
        self.assertTrue(third["queued"])  # 手速上限，停在队列
        self.assertEqual(2, self.db.snapshot()["users"][0]["pending_count"])
        # 提交一份后，手速名额空出，自己在第三条的排队被补入
        submitted = self.db.submit_annotation(i1, a, "中性")
        self.assertEqual([{"item_id": i3, "annotator_id": a}], submitted["promoted"])
        self.assertEqual(2, self.db.snapshot()["users"][0]["pending_count"])

    def test_release_cancels_queue_and_rejects_submitted(self):
        item = self.make_item(2)
        self.db.assign(item, self.anns[0])
        self.db.assign(item, self.anns[1])
        self.db.assign(item, self.anns[2])  # 排队
        self.db.submit_annotation(item, self.anns[0], "正向")
        # 已提交不能交回
        with self.assertRaisesRegex(DomainError, "已提交"):
            self.db.release(item, self.anns[0])
        # 排队中的领取可以直接取消
        canceled = self.db.release(item, self.anns[2])
        self.assertTrue(canceled["canceled_queue"])
        self.assertEqual(0, len(self.db.snapshot()["claim_queue"]))
        # 什么都没领时报错
        with self.assertRaisesRegex(DomainError, "没有领取"):
            self.db.release(item, self.anns[3])

    def test_freeze_reports_where_coverage_falls_short(self):
        mgr = self.db.add_user("管理", "manager")
        # 条目1 需3人：2人提交、丙占住未提交、丁排队；条目2 需2人：1人提交乙占住；条目3 需1人已齐
        i1 = self.make_item(3)
        i2 = self.make_item(2)
        i3 = self.make_item(1)
        self.db.assign(i1, self.anns[0]); self.db.assign(i1, self.anns[1])
        self.db.assign(i1, self.anns[2]); self.db.assign(i1, self.anns[3])
        self.db.submit_annotation(i1, self.anns[0], "正向")
        self.db.submit_annotation(i1, self.anns[1], "正向")
        self.db.assign(i2, self.anns[0]); self.db.assign(i2, self.anns[1])
        self.db.submit_annotation(i2, self.anns[0], "中性")  # 乙还占着 i2 未提交
        self.db.assign(i3, self.anns[0])
        self.db.submit_annotation(i3, self.anns[0], "中性")
        with self.assertRaisesRegex(DomainError, "覆盖人数不足") as ctx:
            self.db.freeze_batch(self.batch, mgr)
        message = str(ctx.exception)
        self.assertIn("条目1需3人、仅2人提交、差1人", message)
        self.assertIn("占住未提交:丙", message)
        self.assertIn("队列等待1人:丁", message)
        self.assertIn("条目2需2人、仅1人提交、差1人", message)
        self.assertIn("占住未提交:乙", message)
        self.assertNotIn("条目3", message)
        # 乙补交；丙交回后丁按 FIFO 补入但仍未提交，冻结还是不通过
        self.db.submit_annotation(i2, self.anns[1], "中性")
        self.db.release(i1, self.anns[2])
        with self.assertRaisesRegex(DomainError, "占住未提交:丁"):
            self.db.freeze_batch(self.batch, mgr)
        self.db.submit_annotation(i1, self.anns[3], "正向")
        frozen = self.db.freeze_batch(self.batch, mgr)
        self.assertEqual(3, len(frozen["metrics"]["items"]))

    def test_single_annotator_item_and_invalid_required(self):
        item = self.db.add_item(self.batch, 99, "仅需一人", 1)
        self.assertEqual("assigned", self.db.assign(item, self.anns[0])["state"])
        self.assertTrue(self.db.assign(item, self.anns[1])["queued"])
        with self.assertRaisesRegex(DomainError, "至少需要一位"):
            self.db.add_item(self.batch, 100, "非法", 0)


if __name__ == "__main__":
    unittest.main()
