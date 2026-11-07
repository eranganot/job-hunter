"""Step 13: a salary read from a job's text, as a monthly range."""
import salary


def test_a_us_annual_range():
    s = salary.parse("Salary\n$200,000 - $250,000 USD\nWhat We Offer")
    assert s == {"currency": "USD", "monthly_min": 16667, "monthly_max": 20833, "period": "year"}


def test_an_israeli_monthly_range_in_shekels():
    s = salary.parse("שכר: ₪30,000-35,000 לחודש")
    assert (s["currency"], s["monthly_min"], s["monthly_max"], s["period"]) == ("ILS", 30000, 35000, "month")
    s = salary.parse("Salary range 28K-32K ILS per month")
    assert (s["monthly_min"], s["monthly_max"]) == (28000, 32000)


def test_k_suffix_and_euro():
    s = salary.parse("Base compensation: €120k–140k per year")
    assert s["currency"] == "EUR" and s["monthly_max"] == 11667


def test_no_salary_and_non_salary_money_are_ignored():
    assert salary.parse("We raised $50M in Series B and are growing fast") is None
    assert salary.parse("Senior Product Manager, Tel Aviv. 5+ years of experience.") is None
    assert salary.parse("") is None
    assert salary.parse("Salary: 30,000 - 40,000") is None      # no currency: not read
