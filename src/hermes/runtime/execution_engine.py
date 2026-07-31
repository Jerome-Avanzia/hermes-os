from hermes.capabilities.generate_design_concepts import generate_design_concepts
from hermes.capabilities.research_etsy_opportunity import research_etsy_opportunity
from hermes.models import Plan, Result

SECTION_TITLES = {
    "Research Etsy Opportunity": "Opportunity Report",
    "Generate Design Concepts": "Design Concepts",
}


def _section(title: str, body: str) -> str:
    divider = "=" * 34
    return f"{divider}\n{title}\n{divider}\n\n{body}"


class ExecutionEngine:
    def execute(self, plan: Plan) -> Result:
        last_output = ""
        sections = []

        for step in plan.steps:
            if step == "Research Etsy Opportunity":
                last_output = research_etsy_opportunity(plan.task.request)
            elif step == "Generate Design Concepts":
                last_output = generate_design_concepts(last_output)
            else:
                return Result(
                    task=plan.task,
                    status="failed",
                    output=f"Unknown step: {step}",
                )

            sections.append(_section(SECTION_TITLES[step], last_output))

        return Result(
            task=plan.task,
            status="success",
            output="\n\n".join(sections),
        )
