import json
import os
import random
import tempfile
import unittest

from corpus_minimizer import (
    CorpusEntry,
    CoverageUnreachableError,
    DropReason,
    InvalidCorpusError,
    PreservationError,
    hash_file,
    load_corpus_directory,
    load_manifest,
    minimize_corpus,
)


def entry(sample_id, **kwargs):
    coverage = kwargs.pop("coverage", frozenset())
    return CorpusEntry(sample_id=sample_id, coverage=coverage, **kwargs)


class EmptyCorpusTests(unittest.TestCase):
    def test_empty_corpus_returns_empty_result(self):
        result = minimize_corpus([])
        self.assertEqual(result.kept_ids, ())
        self.assertEqual(result.removed, {})
        self.assertEqual(result.original_size, 0)

    def test_empty_corpus_cannot_reach_target(self):
        with self.assertRaises(CoverageUnreachableError) as ctx:
            minimize_corpus([], target_coverage=["edge/a"])
        self.assertEqual(ctx.exception.missing, frozenset(["edge/a"]))


class EquivalentSamplesTests(unittest.TestCase):
    def _entries(self):
        return [
            entry("slow", coverage=frozenset({"edge/a"}), duration=9.0, size=10),
            entry("fast", coverage=frozenset({"edge/a"}), duration=1.0, size=10),
            entry("big", coverage=frozenset({"edge/a"}), duration=1.0, size=100),
        ]

    def test_picks_cheapest_equivalent_deterministically(self):
        result = minimize_corpus(self._entries(), target_coverage={"edge/a"})
        self.assertEqual(result.kept_ids, ("fast",))
        self.assertEqual(
            set(result.removed.values()), {DropReason.REDUNDANT}
        )

    def test_selection_independent_of_input_order(self):
        entries = self._entries()
        baseline = minimize_corpus(entries, target_coverage={"edge/a"}).kept_ids

        rng = random.Random(20261116)
        for _ in range(20):
            shuffled = entries[:]
            rng.shuffle(shuffled)
            shuffled_result = minimize_corpus(
                shuffled, target_coverage={"edge/a"}
            )
            self.assertEqual(shuffled_result.kept_ids, baseline)

    def test_tie_breaks_by_sample_id(self):
        entries = [
            entry("zeta", coverage=frozenset({"edge/a"}), duration=1.0),
            entry("alpha", coverage=frozenset({"edge/a"}), duration=1.0),
        ]
        result = minimize_corpus(entries, target_coverage={"edge/a"})
        self.assertEqual(result.kept_ids, ("alpha",))


class ComplementaryCoverageTests(unittest.TestCase):
    def test_union_cover_keeps_only_necessary_samples(self):
        entries = [
            entry("a", coverage=frozenset({"edge/1", "edge/2"})),
            entry("b", coverage=frozenset({"edge/2", "edge/3"})),
            entry("c", coverage=frozenset({"edge/3"})),
        ]
        result = minimize_corpus(entries, target_coverage={"edge/1", "edge/3"})
        self.assertEqual(set(result.kept_ids), {"a", "b"})
        kept_coverage = frozenset(
            element
            for kept in result.kept
            for element in kept.effective_coverage
        )
        self.assertTrue({"edge/1", "edge/3"}.issubset(kept_coverage))

    def test_default_target_is_full_corpus_union(self):
        entries = [
            entry("a", coverage=frozenset({"edge/1"})),
            entry("b", coverage=frozenset({"edge/2"})),
            entry("c", coverage=frozenset({"edge/1", "edge/2"})),
        ]
        result = minimize_corpus(entries)
        self.assertEqual(result.target_coverage, frozenset({"edge/1", "edge/2"}))
        kept_coverage = frozenset(
            element
            for kept in result.kept
            for element in kept.effective_coverage
        )
        self.assertEqual(kept_coverage, frozenset({"edge/1", "edge/2"}))
        self.assertEqual(len(result.kept), 1)


class CrashSignatureTests(unittest.TestCase):
    def test_one_sample_per_signature_is_preserved(self):
        entries = [
            entry("crash-1", crash_signature="SIG-A"),
            entry("crash-2", crash_signature="SIG-A", duration=0.5),
            entry("crash-3", crash_signature="SIG-B"),
        ]
        result = minimize_corpus(entries)
        self.assertEqual(
            result.preserved_signatures, frozenset({"SIG-A", "SIG-B"})
        )
        kept_signatures = {item.crash_signature for item in result.kept}
        self.assertEqual(kept_signatures, {"SIG-A", "SIG-B"})
        self.assertEqual(len(result.kept), 2)

    def test_required_entry_can_cover_crash_signature(self):
        entries = [
            entry("keep-me", crash_signature="SIG-A", required=True),
            entry("duplicate-crash", crash_signature="SIG-A"),
        ]
        result = minimize_corpus(entries)
        self.assertEqual(result.kept_ids, ("keep-me",))

    def test_crash_only_in_oversized_sample_raises(self):
        entries = [
            entry("huge-crash", crash_signature="SIG-A", size=4096),
        ]
        with self.assertRaises(PreservationError):
            minimize_corpus(entries, max_sample_size=1024)


class RequiredFilesTests(unittest.TestCase):
    def test_required_sample_kept_even_without_coverage(self):
        entries = [
            entry("must-keep", required=True),
            entry("useful", coverage=frozenset({"edge/1"})),
        ]
        result = minimize_corpus(entries, target_coverage={"edge/1"})
        self.assertEqual(set(result.kept_ids), {"must-keep", "useful"})

    def test_required_sample_exempt_from_size_limit(self):
        entries = [
            entry("huge-required", size=8192, required=True),
        ]
        result = minimize_corpus(entries, max_sample_size=1024)
        self.assertEqual(result.kept_ids, ("huge-required",))


class DuplicateContentTests(unittest.TestCase):
    def test_duplicate_hashes_merge_with_union_coverage(self):
        entries = [
            entry(
                "copy-a",
                coverage=frozenset({"edge/1"}),
                content_hash="hash-1",
                size=10,
                duration=2.0,
            ),
            entry(
                "copy-b",
                coverage=frozenset({"edge/2"}),
                content_hash="hash-1",
                size=10,
                duration=1.0,
            ),
        ]
        result = minimize_corpus(entries, target_coverage={"edge/1", "edge/2"})
        self.assertEqual(len(result.kept), 1)
        leader = result.kept[0]
        self.assertEqual(
            leader.effective_coverage, frozenset({"edge/1", "edge/2"})
        )
        self.assertNotIn("copy-b", result.removed)
        self.assertEqual(result.merged_into, {"copy-b": "copy-a"})
        self.assertEqual(result.duplicate_count, 1)

    def test_duplicate_group_preserves_required_flag(self):
        entries = [
            entry("a", content_hash="h", required=False),
            entry("b", content_hash="h", required=True),
        ]
        result = minimize_corpus(entries)
        self.assertEqual(len(result.kept), 1)
        self.assertTrue(result.kept[0].required)

    def test_whole_duplicate_group_oversized_is_stable(self):
        entries = [
            entry("a", content_hash="h", size=4096),
            entry("b", content_hash="h", size=4096),
        ]
        result = minimize_corpus(entries, max_sample_size=1024)
        self.assertEqual(result.kept_ids, ())
        self.assertEqual(
            result.removed,
            {"a": DropReason.OVERSIZED, "b": DropReason.OVERSIZED},
        )
        self.assertEqual(result.merged_into, {})

        shuffled = [entries[1], entries[0]]
        shuffled_result = minimize_corpus(shuffled, max_sample_size=1024)
        self.assertEqual(shuffled_result.removed, result.removed)


class OversizedAndFailureTests(unittest.TestCase):
    def test_oversized_sample_dropped(self):
        entries = [
            entry("small", coverage=frozenset({"edge/1"}), size=10),
            entry("large", coverage=frozenset({"edge/2"}), size=4096),
        ]
        result = minimize_corpus(
            entries, target_coverage={"edge/1"}, max_sample_size=1024
        )
        self.assertEqual(result.kept_ids, ("small",))
        self.assertEqual(result.removed["large"], DropReason.OVERSIZED)

    def test_oversized_only_cover_raises_unreachable(self):
        entries = [
            entry("large", coverage=frozenset({"edge/rare"}), size=4096),
        ]
        with self.assertRaises(CoverageUnreachableError):
            minimize_corpus(
                entries, target_coverage={"edge/rare"}, max_sample_size=1024
            )

    def test_failed_sample_without_coverage_is_dropped(self):
        entries = [
            entry("broken", execution_failed=True),
            entry("good", coverage=frozenset({"edge/1"})),
        ]
        result = minimize_corpus(entries, target_coverage={"edge/1"})
        self.assertEqual(result.kept_ids, ("good",))
        self.assertEqual(
            result.removed["broken"], DropReason.EXECUTION_FAILED
        )

    def test_failed_sample_with_coverage_still_usable(self):
        entries = [
            entry(
                "flaky",
                coverage=frozenset({"edge/1"}),
                execution_failed=True,
            ),
        ]
        result = minimize_corpus(entries, target_coverage={"edge/1"})
        self.assertEqual(result.kept_ids, ("flaky",))

    def test_failed_required_sample_is_kept(self):
        entries = [
            entry("broken-required", execution_failed=True, required=True),
        ]
        result = minimize_corpus(entries)
        self.assertEqual(result.kept_ids, ("broken-required",))

    def test_missing_coverage_treated_as_empty(self):
        entries = [
            CorpusEntry(sample_id="no-coverage", coverage=None),
            entry("covered", coverage=frozenset({"edge/1"})),
        ]
        result = minimize_corpus(entries, target_coverage={"edge/1"})
        self.assertEqual(result.kept_ids, ("covered",))
        self.assertEqual(result.removed["no-coverage"], DropReason.REDUNDANT)


class ValidationTests(unittest.TestCase):
    def test_duplicate_sample_id_rejected(self):
        entries = [
            entry("same-id", coverage=frozenset({"edge/1"})),
            entry("same-id", coverage=frozenset({"edge/2"})),
        ]
        with self.assertRaises(InvalidCorpusError):
            minimize_corpus(entries)

    def test_negative_duration_rejected(self):
        with self.assertRaises(InvalidCorpusError):
            minimize_corpus([entry("bad", duration=-1.0)])


class ManifestLoadingTests(unittest.TestCase):
    def test_load_manifest_and_minimize(self):
        records = {
            "entries": [
                {
                    "sample_id": "a",
                    "coverage": ["edge/1", "edge/2"],
                    "duration": 1.5,
                },
                {"sample_id": "b", "coverage": ["edge/2"]},
                {"sample_id": "c", "crash_signature": "SIG-X"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = os.path.join(tmp, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(records, handle)

            loaded = load_manifest(manifest_path)
            result = minimize_corpus(
                loaded, target_coverage={"edge/1", "edge/2"}
            )
            self.assertEqual(set(result.kept_ids), {"a", "c"})

    def test_load_directory_hashes_and_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "z-file"), "wb") as handle:
                handle.write(b"identical bytes")
            with open(os.path.join(tmp, "a-file"), "wb") as handle:
                handle.write(b"identical bytes")
            with open(os.path.join(tmp, "unique"), "wb") as handle:
                handle.write(b"different payload")

            metadata = {
                "a-file": {"coverage": ["edge/1"], "duration": 0.2},
                "z-file": {"coverage": ["edge/2"], "duration": 0.9},
                "unique": {"coverage": ["edge/3"], "required": True},
            }
            loaded = load_corpus_directory(tmp, metadata)

            self.assertEqual(
                [item.sample_id for item in loaded],
                ["a-file", "unique", "z-file"],
            )
            self.assertEqual(
                hash_file(os.path.join(tmp, "a-file")),
                hash_file(os.path.join(tmp, "z-file")),
            )

            result = minimize_corpus(
                loaded, target_coverage={"edge/1", "edge/2", "edge/3"}
            )
            self.assertEqual(len(result.kept), 2)
            self.assertIn("z-file", result.merged_into)


if __name__ == "__main__":
    unittest.main()
