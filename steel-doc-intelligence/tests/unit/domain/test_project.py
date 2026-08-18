import pytest

from src.domain.entities.project import (
    Drawing,
    Project,
    ProjectMembership,
    ProjectRole,
    ProjectStatus,
    revision_sort_index,
)


class TestRevisionSortIndex:
    """Revision labels are not consistent across practices. Sorting the label
    text would put "B" after "10" and "C1" before "P1", so ordering uses a
    derived integer."""

    @pytest.mark.parametrize(
        "earlier,later",
        [
            ("A", "B"),
            ("B", "C"),
            ("0", "1"),
            ("1", "2"),
            ("9", "10"),
            ("P1", "C1"),
            ("P2", "C1"),
            ("C1", "C2"),
        ],
    )
    def test_ordering(self, earlier, later):
        assert revision_sort_index(earlier) < revision_sort_index(later)

    def test_a_construction_issue_supersedes_any_preliminary(self):
        """Stage beats number: C1 is later than P9, even though 1 < 9."""
        assert revision_sort_index("C1") > revision_sort_index("P9")

    def test_case_and_separators_do_not_matter(self):
        assert revision_sort_index("c1") == revision_sort_index("C-1") == revision_sort_index("C 1")

    def test_an_unrecognised_scheme_sorts_last(self):
        """A scheme we do not understand is more likely a recent addition
        than an early draft. Sorting it first would silently mark a current
        sheet as superseded."""
        assert revision_sort_index("ISSUE-FOR-TENDER") > revision_sort_index("Z")

    def test_an_empty_label_sorts_first(self):
        assert revision_sort_index("") == 0


class TestProject:
    def test_a_new_project_is_active(self):
        assert Project(project_number="J-2024-0117", name="Roof").is_active

    def test_an_archived_project_is_not_active(self):
        project = Project(project_number="J-1", name="Old", status=ProjectStatus.ARCHIVED)

        assert not project.is_active


class TestMembership:
    @pytest.mark.parametrize(
        "role,expected", [(ProjectRole.OWNER, True), (ProjectRole.CONTRIBUTOR, True), (ProjectRole.READER, False)]
    )
    def test_upload_rights_follow_the_project_role(self, role, expected, sample_user_id):
        import uuid

        membership = ProjectMembership(
            project_id=uuid.uuid4(), user_id=sample_user_id, project_role=role
        )

        assert membership.can_upload is expected


class TestDrawing:
    def test_label_includes_the_sheet_when_present(self):
        assert Drawing(drawing_number="S-104", sheet_number="2").label == "S-104/2"

    def test_label_is_just_the_number_otherwise(self):
        assert Drawing(drawing_number="S-104").label == "S-104"
