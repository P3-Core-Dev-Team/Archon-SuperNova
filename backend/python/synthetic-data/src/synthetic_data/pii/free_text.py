"""Free-text PII injection: embeds real PII values inside review/ticket prose."""

from __future__ import annotations

import random as stdlib_random
from faker import Faker


# Sentence templates with placeholders
_TEMPLATES_EMAIL = [
    "Please contact me at {email} for a follow-up.",
    "My email is {email} and I am waiting for a response.",
    "You can reach me at {email} if you have any questions.",
    "Feel free to email me at {email} regarding this issue.",
    "I can be contacted via {email} for further details.",
]

_TEMPLATES_PHONE = [
    "Call me back at {phone} at your earliest convenience.",
    "My phone number is {phone}.",
    "You can reach me by phone: {phone}.",
    "Please call {phone} to resolve this matter.",
    "My contact number is {phone}.",
]

_TEMPLATES_NAME = [
    "My name is {name} and I have been a customer for years.",
    "This is {name} reaching out about my recent order.",
    "{name} here — I need help with my account.",
    "I am {name} and I am very dissatisfied with this service.",
]

_TEMPLATES_PLAIN = [
    "I really enjoyed this product, it works exactly as described.",
    "The quality exceeded my expectations and shipping was fast.",
    "Decent product but packaging could be better.",
    "Works fine but instructions were not clear.",
    "Five stars — would recommend to friends and family.",
    "Not what I expected based on the description, but usable.",
    "I had a great experience with customer support.",
    "Product arrived damaged, waiting for replacement.",
    "Excellent build quality and fast delivery.",
    "The product stopped working after two weeks.",
    "Great value for money, very happy with purchase.",
    "Item is exactly as pictured, no issues.",
    "Customer service was very helpful and responsive.",
    "Would definitely buy again, very satisfied.",
    "Mediocre quality but acceptable for the price.",
]


def generate_free_text(
    pii_rate: float = 0.06,
    rng: stdlib_random.Random | None = None,
    fake: Faker | None = None,
) -> str:
    """
    Generate a free-text string (review or ticket body).
    With probability `pii_rate`, embed real PII in the text.
    """
    if rng is None:
        rng = stdlib_random.Random()
    if fake is None:
        fake = Faker("en_US")

    if rng.random() < pii_rate:
        pii_kind = rng.choice(["email", "phone", "name"])
        base = rng.choice(_TEMPLATES_PLAIN)
        if pii_kind == "email":
            template = rng.choice(_TEMPLATES_EMAIL)
            pii_val = fake.email()
            return base + " " + template.format(email=pii_val)
        elif pii_kind == "phone":
            template = rng.choice(_TEMPLATES_PHONE)
            # Use faker phone for variety
            pii_val = fake.phone_number()
            return base + " " + template.format(phone=pii_val)
        else:
            template = rng.choice(_TEMPLATES_NAME)
            pii_val = fake.name()
            return template.format(name=pii_val) + " " + base
    else:
        return rng.choice(_TEMPLATES_PLAIN)


def generate_free_text_batch(
    n: int,
    pii_rate: float = 0.06,
    rng: stdlib_random.Random | None = None,
    fake: Faker | None = None,
) -> list[str]:
    """Generate n free-text strings."""
    if rng is None:
        rng = stdlib_random.Random()
    if fake is None:
        fake = Faker("en_US")
    return [generate_free_text(pii_rate=pii_rate, rng=rng, fake=fake) for _ in range(n)]
