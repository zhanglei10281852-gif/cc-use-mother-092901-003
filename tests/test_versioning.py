import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from scenario_certification import NotFoundError
from support import make_content, new_backend, register_scenario


class ScenarioVersioningTests(unittest.TestCase):
    """版本分叉：同一父版本可分出多支，谱系与叶版本的推导保持确定。"""

    def test_forked_revisions_share_parent_and_have_distinct_lineages(self):
        backend = new_backend()
        register_scenario(backend, "SCN-1", tag="v1")
        rev2 = backend.scenarios.publish_revision(
            "SCN-1", 1, make_content("v2-mainline"), "qa", "主分支演进"
        )
        rev3 = backend.scenarios.publish_revision(
            "SCN-1", 1, make_content("v2-fork", fault="sensor-dropout"), "qa", "故障注入分叉"
        )

        self.assertEqual((rev2.version, rev2.parent_version), (2, 1))
        self.assertEqual((rev3.version, rev3.parent_version), (3, 1))
        self.assertNotEqual(rev2.content_hash, rev3.content_hash)

        lineage2 = backend.scenarios.lineage("SCN-1", 2)
        lineage3 = backend.scenarios.lineage("SCN-1", 3)
        self.assertEqual([r.version for r in lineage2], [1, 2])
        self.assertEqual([r.version for r in lineage3], [1, 3])
        self.assertEqual(backend.scenarios.heads("SCN-1"), (2, 3))

    def test_deeper_lineage_tracks_through_forks(self):
        backend = new_backend()
        register_scenario(backend, "SCN-1", tag="v1")
        backend.scenarios.publish_revision("SCN-1", 1, make_content("v2"), "qa", "第二版")
        rev3 = backend.scenarios.publish_revision("SCN-1", 2, make_content("v3"), "qa", "第三版")
        backend.scenarios.publish_revision("SCN-1", 1, make_content("v2-fork"), "qa", "自首版分叉")

        self.assertEqual([r.version for r in backend.scenarios.lineage("SCN-1", rev3.version)], [1, 2, 3])
        self.assertEqual(backend.scenarios.heads("SCN-1"), (3, 4))

    def test_content_digest_is_canonical(self):
        content_a = make_content("same", ttc=1.5)
        content_b = make_content("same", ttc=1.5)
        self.assertEqual(content_a.digest(), content_b.digest())
        self.assertNotEqual(content_a.digest(), make_content("same", ttc=2.0).digest())

    def test_unknown_scenario_or_base_version_rejected(self):
        backend = new_backend()
        register_scenario(backend, "SCN-1")
        with self.assertRaises(NotFoundError):
            backend.scenarios.publish_revision("SCN-X", 1, make_content(), "qa", "不存在")
        with self.assertRaises(NotFoundError):
            backend.scenarios.publish_revision("SCN-1", 9, make_content(), "qa", "基版本不存在")
        with self.assertRaises(NotFoundError):
            backend.scenarios.lineage("SCN-1", 7)


if __name__ == "__main__":
    unittest.main()
