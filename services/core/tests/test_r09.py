"""R09: a weak-skill request is not fulfilled by material that tests another skill."""

from test_pipeline import SPEC, demo_providers, enqueue, unit_of

from lq.generator import factory
from lq.generator.demo import DemoContent
from lq.jobs import worker


class OtherSkillContent(DemoContent):
    """Always writes questions that need main_idea, whatever was asked."""

    def __init__(self):
        self.calls = 0

    def generate(self, spec, meter):
        self.calls += 1
        data = super().generate(spec, meter)
        for q in data["questions"]:
            q["skill_tags"] = ["main_idea"]
        data["skill_tags"], data["primary_skill"] = ["main_idea"], "main_idea"
        from lq.domain.content import UnitContent
        from lq.generator.demo import register_demo_oracle

        register_demo_oracle(UnitContent.model_validate(data))
        return data


def test_requested_skill_not_tested_is_kept_as_other_stock_and_stays_unfulfilled(db, user):
    prov = OtherSkillContent()
    factory.set_provider_override("demo", demo_providers(content=prov))
    jid = enqueue(db, primary_skill="numbers_negation", seed="r09")
    assert worker.drain("w") == ["succeeded"]
    job, unit = unit_of(db, jid)
    assert prov.calls == 2  # one rewrite inside the bounded attempts, no more
    assert job.state["skill_request"] == {"requested": "numbers_negation", "validated": "main_idea", "fulfilled": False}
    assert unit.primary_skill == "main_idea" and "numbers_negation" not in unit.skill_tags  # no tag added afterwards
    from lq.services import admin

    assert admin.job_dto(db, job)["skill_request"]["fulfilled"] is False


def test_requested_skill_that_is_tested_is_fulfilled(db, user):
    jid = enqueue(db, **{**SPEC, "seed": "r09ok"})
    assert worker.drain("w") == ["succeeded"]
    job, unit = unit_of(db, jid)
    assert job.state["skill_request"]["fulfilled"] is True and unit.primary_skill == SPEC["primary_skill"]
