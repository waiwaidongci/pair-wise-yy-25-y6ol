import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import CorpusDB, DomainError


class CorpusFlowTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = CorpusDB(self.path)
        self.a1 = self.db.add_user("甲", "annotator")
        self.a2 = self.db.add_user("乙", "annotator")
        self.arb = self.db.add_user("仲裁", "arbitrator")
        self.mgr = self.db.add_user("管理", "manager")
        self.g = self.db.add_guideline("v1", "独立标注")
        self.batch = self.db.create_batch("测试批次", self.g)
        self.item1 = self.db.add_item(self.batch, 1, "这个版本很快。")
        self.item2 = self.db.add_item(self.batch, 2, "没有明显变化。")

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def test_full_annotation_disagreement_adjudication_freeze_flow(self):
        for item in (self.item1, self.item2):
            self.db.assign(item, self.a1)
            self.db.assign(item, self.a2)
        self.db.submit_annotation(self.item1, self.a1, "正向")
        self.db.submit_annotation(self.item1, self.a2, "中性")
        self.db.submit_annotation(self.item2, self.a1, "中性")
        self.db.submit_annotation(self.item2, self.a2, "中性")
        self.assertEqual(1, len(self.db.disagreements(self.batch)))
        with self.assertRaisesRegex(DomainError, "分歧"):
            self.db.freeze_batch(self.batch, self.mgr)
        self.db.adjudicate(self.item1, "正向", "速度描述构成明确正向倾向", self.arb)
        result = self.db.freeze_batch(self.batch, self.mgr)
        self.assertIsNotNone(result["metrics"]["pairwise_agreement"])
        exported = self.db.export_gold(self.batch)
        self.assertEqual(2, len(exported["records"]))
        self.assertEqual("adjudication", exported["records"][0]["source"])

    def test_answer_isolation_and_role_validation(self):
        self.db.assign(self.item1, self.a1)
        self.db.assign(self.item1, self.a2)
        self.db.add_discussion(self.item1, self.a2, "我认为是正向", True)
        secret = self.db.get_item_for_user(self.item1, self.a1)
        self.assertTrue(secret["discussions"][0]["hidden"])
        self.db.submit_annotation(self.item1, self.a1, "负向")
        visible = self.db.get_item_for_user(self.item1, self.a1)
        self.assertFalse(visible["discussions"][0].get("hidden", False))
        with self.assertRaisesRegex(DomainError, "标注员"):
            self.db.assign(self.item2, self.arb)

    def test_claim_limits_queue_and_return_promotes(self):
        a3 = self.db.add_user("丙", "annotator")
        item3 = self.db.add_item(self.batch, 3, "第三条文本。")
        # 甲领取两条后达到个人待办上限，再领只能排队
        self.assertEqual("assigned", self.db.assign(self.item1, self.a1)["status"])
        self.assertEqual("assigned", self.db.assign(self.item2, self.a1)["status"])
        self.assertEqual("queued", self.db.assign(item3, self.a1)["status"])
        # 乙领走条目1最后一个名额，丙只能排队；重复领取保持排队，不重复占位
        self.assertEqual("assigned", self.db.assign(self.item1, self.a2)["status"])
        self.assertEqual("queued", self.db.assign(self.item1, a3)["status"])
        self.assertEqual("queued", self.db.assign(self.item1, a3)["status"])
        snap = self.db.snapshot()
        self.assertEqual(2, len(snap["queue"]))
        self.assertEqual(2, next(u["pending"] for u in snap["users"] if u["id"] == self.a1))
        self.assertEqual(0, next(i for i in snap["items"] if i["id"] == self.item1)["needed"])
        self.assertEqual(1, next(i for i in snap["items"] if i["id"] == self.item2)["needed"])
        # 提交释放个人名额，甲排队的条目3被补位；条目1名额仍满，丙继续排队
        self.db.submit_annotation(self.item1, self.a1, "正向")
        snap = self.db.snapshot()
        self.assertEqual(2, next(u["pending"] for u in snap["users"] if u["id"] == self.a1))
        self.assertEqual(1, next(i for i in snap["items"] if i["id"] == item3)["active_assignments"])
        self.assertEqual(1, len(snap["queue"]))
        # 乙交回未提交任务，名额立即给排队的丙
        result = self.db.return_task(self.item1, self.a2)
        self.assertEqual([{"item_id": self.item1, "annotator_id": a3}], result["promoted"])
        snap = self.db.snapshot()
        self.assertEqual(0, next(i for i in snap["items"] if i["id"] == self.item1)["needed"])
        self.assertEqual(0, len(snap["queue"]))
        # 已提交的任务不能交回
        with self.assertRaisesRegex(DomainError, "已提交"):
            self.db.return_task(self.item1, self.a1)

    def test_freeze_explains_coverage_shortfall(self):
        a3 = self.db.add_user("丙", "annotator")
        item3 = self.db.add_item(self.batch, 3, "需要三人覆盖。", required_annotators=3)
        for item in (self.item1, self.item2):
            self.db.assign(item, self.a1)
            self.db.assign(item, self.a2)
            self.db.submit_annotation(item, self.a1, "中性")
            self.db.submit_annotation(item, self.a2, "中性")
        self.db.assign(item3, self.a1)
        self.db.assign(item3, self.a2)
        self.db.submit_annotation(item3, self.a1, "中性")
        self.db.submit_annotation(item3, self.a2, "中性")
        with self.assertRaisesRegex(DomainError, "覆盖人数不足.*还差1人"):
            self.db.freeze_batch(self.batch, self.mgr)
        self.db.assign(item3, a3)
        self.db.submit_annotation(item3, a3, "中性")
        result = self.db.freeze_batch(self.batch, self.mgr)
        self.assertIsNotNone(result["frozen_at"])


if __name__ == "__main__":
    unittest.main()
