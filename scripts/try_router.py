"""Smoke test the Router against the real API with labeled messages.

These messages are deliberately NOT the ones in ROUTER_SYSTEM_PROMPT —
testing on the prompt's own examples would only measure memorization.
"""
from second_brain.llm_client import LLMClient
from second_brain.models import Intent
from second_brain.orchestration.router import Router

CASES: list[tuple[str, Intent]] = [
    # capture
    ("Aklıma geldi: kampüste ikinci el kitap takas rafı kurulabilir", Intent.CAPTURE),
    ("https://arxiv.org/abs/2401.00001 ilginç görünüyor", Intent.CAPTURE),
    # query
    ("Davranışsal ekonomi hakkında neler kaydetmiştim?", Intent.QUERY),
    ("Geçen ay okuduğum kitap önerisi neydi?", Intent.QUERY),
    # task
    ("Laptopun şarj aletini tamir ettirmem lazım", Intent.TASK),
    ("CV'mi güncelle", Intent.TASK),
    # reminder
    ("Perşembe 14:00'te danışmanımla toplantıyı hatırlat", Intent.REMINDER),
    ("Yarın sabah ilaç almayı unutmayayım", Intent.REMINDER),
    # journal
    ("Bugün kütüphanede verimli çalıştım, akşam da koşuya çıktım", Intent.JOURNAL),
    ("Bugün biraz buruktum, eski bir arkadaşımla konuştum", Intent.JOURNAL),
    # state
    ("Önümüzdeki iki hafta final dönemim", Intent.STATE),
    ("Hastayım, bu hafta düşük tempodayım", Intent.STATE),
    # command
    ("Hafta sonu bana bildirim gönderme", Intent.COMMAND),
    ("/start", Intent.COMMAND),
]


def main() -> None:
    router = Router(LLMClient())
    correct = 0

    for message, expected in CASES:
        got = router.classify(message)
        ok = got is expected
        correct += ok
        mark = "✓" if ok else "✗"
        print(f"{mark} {got.value:<9} (expected {expected.value:<9}) {message}")

    print(f"\n{correct}/{len(CASES)} correct ({correct / len(CASES):.0%})")


if __name__ == "__main__":
    main()
    