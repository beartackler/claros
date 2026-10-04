"""Unit tests for the eval suite's metric helpers and ground-truth oracles (infra/evals)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra" / "evals"))

import common  # noqa: E402
import tutor_eval  # noqa: E402


def test_cer_and_number_parsing():
    assert common.cer("8.400,00", "8.400,00") == 0
    assert common.cer("8.400,00", "8.4OO,00") == 0.25
    assert common.best_substring_cer("8.400,00", ["€ 8.400,00 total"])[0] == 0.0
    for s, v in (("8.400,00", 8400.0), ("8,400.00", 8400.0), ("€ 1.240,50", 1240.5), ("50,00 EUR", 50.0),
                 ("7200", 7200.0), ("2.850", 2850.0)):
        assert common.parse_num(s) == v, s


def test_value_match_is_locale_tolerant_but_exact():
    assert common.value_match("2026-10-01", "01.10.2026") == (True, True)
    assert common.value_match(8400.0, "€ 8.400,00") == (True, True)
    assert common.value_match("Tools and Small Equipment - OPP", "Tools and Small Equipment") == (False, True)
    assert common.value_match("Feldmark Maschinentechnik GmbH", "01.10.2026") == (False, False)


def test_prf_and_wilson():
    assert common.prf(8, 2, 2)["precision"] == 0.8
    lo, hi = common.wilson(1, 1)
    assert lo < 0.3 and hi == 1.0  # n=1 says almost nothing


def test_erp_oracle_boundaries():
    by = {c["id"]: c for c in tutor_eval.erp_cases()}
    assert not by["capex_opex_4999"]["should_intervene"] and not by["capex_opex_5000"]["should_intervene"]
    assert by["capex_opex_5000.01"]["violations"] == ["R_capex"]
    assert not by["capex_capitalised_7200"]["should_intervene"]
    assert not by["maint_6100"]["should_intervene"]
    assert by["uk_pennick_1450"]["violations"] == ["R_uk"]
    assert by["dup_velt_2025-12-15_2850"]["violations"] == ["R_dup"]
    assert not by["dup_velt_2026-03-10_2850"]["should_intervene"]
    assert not by["dup_velt_2025-12-20_2851"]["should_intervene"]
    assert len(by) >= 40


def test_zammad_oracle_boundaries():
    by = {c["id"]: c for c in tutor_eval.zam_cases()}
    assert not by["amt_50_EUR"]["should_intervene"] and by["amt_51_EUR"]["violations"] == ["R_limit"]
    assert not by["days_14_days_ago"]["should_intervene"] and by["days_15_days_ago"]["violations"] == ["R_days"]
    assert by["cb_bank"]["violations"] == ["R_cb"] and not by["cb_negated"]["should_intervene"]
    assert not by["esc_240"]["should_intervene"] and by["esc_240"]["policy_escalate"]
    assert len(by) >= 40 and sum(c["lang"] == "ru" for c in by.values()) >= 8


def test_guardrail_rule_mapping_handles_combined_rules():
    g = {"text": "Tier one may refund only up to fifty euros, within fourteen days, first refund in twelve months"}
    assert set(tutor_eval.rules_of_guardrail(g, "zammad")) >= {"R_limit", "R_days", "R_prior"}
