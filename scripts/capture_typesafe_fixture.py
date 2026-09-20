"""Capture one real SystemOneResponse as a test fixture. Requires TYPESAFE_API_KEY."""

import asyncio
import json
import pathlib

from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score

OUT = pathlib.Path(__file__).parent.parent / "tests/fixtures/responses/smoke_three_questions.json"


async def main() -> None:
    async with AsyncTypeSafeClient() as client:
        response = await client.system_one(
            state={
                "task_goal": "Open the settings page.",
                "screen_elements": [
                    '[e0] button "Settings"',
                    '[e1] button "Cancel"',
                    '[e2] link "Help"',
                ],
                "actions_already_taken": [],
            },
            questions={
                "next_action": Choice(
                    instructions="Which action moves the task forward?",
                    criteria={
                        'Click the button labelled "Settings"': None,
                        'Click the button labelled "Cancel"': None,
                        'Click the link labelled "Help"': None,
                    },
                ),
                "task_complete": Noul(
                    instructions="Does the screen show that the settings page is already open?"
                ),
                "progress": Score(
                    instructions="How far along is the task?",
                    criteria=[
                        "No progress has been made.",
                        "The relevant area of the application is visible.",
                        "The task is partly done.",
                        "The task is nearly done.",
                        "The task is finished.",
                    ],
                ),
            },
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(response.model_dump(mode="json"), indent=2) + "\n")
    print(f"model={response.model} tokens={response.usage.input_tokens} -> {OUT}")
    print(f"  choice   = {response.choices['next_action'].choice!r}")
    print(f"  conf     = {response.choices['next_action'].confidence:.3f}")
    print(f"  complete = {response.nouls['task_complete'].noul:.3f}")
    print(f"  progress = {response.scores['progress'].score:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
