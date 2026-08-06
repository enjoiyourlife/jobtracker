"""
Answer-bank tailoring tests.

select_variant and Profile.load are pure and tested directly.
tailor_answer() makes a real API call in production, so it's tested
here against a stub client shaped like the Anthropic SDK's response —
no network, no key, and no cost, mirroring how the ATS parser tests
run offline against committed fixtures rather than live boards.
"""

from __future__ import annotations

import pytest

from jobtracker.tailor import (
    Profile,
    ProfileError,
    SYSTEM_PROMPT,
    TailorError,
    select_variant,
    tailor_answer,
)

ANSWERS_YAML = """
boilerplate:
  work_authorization: "Authorized to work in the US."

questions:
  - id: why_company
    prompts: [why do you want to work here]
    variants:
      - id: why_company_product
        use_when: [consumer, product, saas]
        text: "Product answer."
      - id: why_company_infra
        use_when: [infrastructure, platform]
        text: "Infra answer."
      - id: why_company_default
        use_when: []
        text: "Default answer."

  - id: challenging_project
    prompts: [describe a challenge]
    variants:
      - id: project_backend
        use_when: [python, backend]
        project_ref: pipeline
        text: "Backend project answer."
"""

PROJECTS_YAML = """
projects:
  - id: pipeline
    name: Pipeline
    summary: A data pipeline.
"""


@pytest.fixture
def profile_dir(tmp_path):
    (tmp_path / "answers.yaml").write_text(ANSWERS_YAML)
    (tmp_path / "projects.yaml").write_text(PROJECTS_YAML)
    return tmp_path


class TestProfileLoad:
    def test_loads_boilerplate(self, profile_dir):
        profile = Profile.load(profile_dir)
        assert profile.boilerplate["work_authorization"] == "Authorized to work in the US."

    def test_loads_questions_and_variants(self, profile_dir):
        profile = Profile.load(profile_dir)
        assert [q.id for q in profile.questions] == ["why_company", "challenging_project"]
        assert len(profile.questions[0].variants) == 3

    def test_loads_projects(self, profile_dir):
        profile = Profile.load(profile_dir)
        assert profile.projects["pipeline"]["name"] == "Pipeline"

    def test_missing_profile_raises(self, tmp_path):
        with pytest.raises(ProfileError):
            Profile.load(tmp_path / "nope")

    def test_missing_projects_file_is_optional(self, tmp_path):
        (tmp_path / "answers.yaml").write_text(ANSWERS_YAML)
        assert Profile.load(tmp_path).projects == {}


class TestSelectVariant:
    def test_matches_by_tag_overlap(self, profile_dir):
        why_company = Profile.load(profile_dir).questions[0]
        variant = select_variant(why_company, "Software Engineer", "A SaaS product company.")
        assert variant.id == "why_company_product"

    def test_infra_posting_selects_infra_variant(self, profile_dir):
        why_company = Profile.load(profile_dir).questions[0]
        variant = select_variant(why_company, "Platform Engineer", "Infrastructure team.")
        assert variant.id == "why_company_infra"

    def test_no_overlap_falls_back_to_universal_variant(self, profile_dir):
        """
        Regression: max() picked whichever tagged variant was listed
        first when nothing actually matched, silently ignoring the
        empty-use_when variant that exists specifically for this case.
        """
        why_company = Profile.load(profile_dir).questions[0]
        variant = select_variant(why_company, "Data Scientist", "Nothing overlapping here.")
        assert variant.id == "why_company_default"

    def test_tagged_variant_beats_universal_fallback(self, profile_dir):
        """A real tag match must outrank the always-applies variant."""
        why_company = Profile.load(profile_dir).questions[0]
        variant = select_variant(why_company, "Consumer App Engineer", None)
        assert variant.id == "why_company_product"

    def test_none_description_does_not_crash(self, profile_dir):
        why_company = Profile.load(profile_dir).questions[0]
        select_variant(why_company, "Software Engineer", None)


class _StubMessage:
    def __init__(self, text: str) -> None:
        self.content = [type("Block", (), {"text": text})()]


class _StubMessages:
    def __init__(self, text: str, capture: dict) -> None:
        self._text = text
        self._capture = capture

    def create(self, **kwargs):
        self._capture.update(kwargs)
        return _StubMessage(self._text)


class _StubClient:
    def __init__(self, text: str = "Tailored response.") -> None:
        self.captured: dict = {}
        self.messages = _StubMessages(text, self.captured)


class TestTailorAnswer:
    def test_returns_model_text(self, profile_dir):
        profile = Profile.load(profile_dir)
        variant = profile.questions[0].variants[0]
        client = _StubClient("A tailored answer.")

        result = tailor_answer(
            variant, None,
            company="Acme", title="Software Engineer", description="Build things.",
            client=client,
        )
        assert result == "A tailored answer."

    def test_sends_the_safety_system_prompt(self, profile_dir):
        """
        The 'do not invent facts' instruction must actually reach the
        API on every call — this is the one thing standing between
        tailoring and a hallucinated claim in a real application.
        """
        profile = Profile.load(profile_dir)
        variant = profile.questions[0].variants[0]
        client = _StubClient()

        tailor_answer(
            variant, None,
            company="Acme", title="Software Engineer", description=None,
            client=client,
        )
        assert client.captured["system"] == SYSTEM_PROMPT
        assert "do not invent" in SYSTEM_PROMPT.lower()

    def test_includes_project_detail_when_referenced(self, profile_dir):
        profile = Profile.load(profile_dir)
        variant = profile.questions[1].variants[0]  # project_backend, refs "pipeline"
        project = profile.projects[variant.project_ref]
        client = _StubClient()

        tailor_answer(
            variant, project,
            company="Acme", title="Backend Engineer", description=None,
            client=client,
        )
        user_content = client.captured["messages"][0]["content"]
        assert "Pipeline" in user_content
        assert variant.text in user_content

    def test_api_error_becomes_tailor_error(self, profile_dir, monkeypatch):
        import anthropic

        profile = Profile.load(profile_dir)
        variant = profile.questions[0].variants[0]

        class _FailingMessages:
            def create(self, **kwargs):
                raise anthropic.APIConnectionError(request=object())

        class _FailingClient:
            messages = _FailingMessages()

        with pytest.raises(TailorError):
            tailor_answer(
                variant, None,
                company="Acme", title="Software Engineer", description=None,
                client=_FailingClient(),
            )
