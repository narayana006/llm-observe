"""Offline demo: a fake client and a fake function, both observed.

Run from the repository root:

    python examples/demo.py
    llm-observe list --db examples/demo.db

Nothing here talks to a real model. In your own code, pass the client or
function you already use to observe() in the same way.
"""

import time
from pathlib import Path

from llm_observe import observe

DB = Path(__file__).parent / "demo.db"
PRICES = {"small-model": {"input": 0.50, "output": 1.50}}  # made-up USD per 1M tokens


class FakeChat:
    def create(self, model: str, messages: list[dict]) -> dict:
        question = messages[-1]["content"]
        time.sleep(0.02)
        if "crash" in question:
            raise TimeoutError("upstream timed out after 30s")
        answer = f"Here is a short answer to: {question}"
        return {
            "model": model,
            "text": answer,
            "usage": {"input_tokens": 12 * len(question.split()), "output_tokens": 9 * len(answer.split())},
        }


class FakeClient:
    """Shaped like a typical model client: client.chat.create(...)."""

    def __init__(self) -> None:
        self.chat = FakeChat()


@observe(name="summarize", model="small-model", db_path=DB, prices=PRICES, usage=lambda text: (40, len(text.split())))
def summarize(text: str) -> str:
    time.sleep(0.01)
    return "Summary: " + " ".join(text.split()[:6]) + "..."


def main() -> None:
    DB.unlink(missing_ok=True)  # start fresh so ids match the README
    client = observe(FakeClient(), name="my_model", db_path=DB, prices=PRICES)

    for question in [
        "How do I request a refund for order A-1042?",
        "What are your opening hours on public holidays?",
        "Please crash now",
    ]:
        try:
            client.chat.create(model="small-model", messages=[{"role": "user", "content": question}])
        except TimeoutError as exc:
            print(f"call failed (and was recorded): {exc}")

    summarize("The customer reports that the parcel arrived damaged and asks for a refund or a replacement.")
    print(f"4 calls recorded in {DB}")


if __name__ == "__main__":
    main()
