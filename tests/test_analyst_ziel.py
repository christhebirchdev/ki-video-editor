"""Tests fuer die Zielsteuerung der Bewertung (Analyst V3).

Laufen ohne API-Keys: nur Schema-Konstanten und reine Nachbearbeitungs-Funktionen.
"""
from models.analyst import ZIELE, SCORE_GEWICHTE_JE_ZIEL


def test_ziele_sind_die_drei_funnel_stufen():
    assert ZIELE == ("TOFU", "MOFU", "BOFU")


def test_jede_zielspalte_summiert_auf_100():
    for ziel in ZIELE:
        assert sum(SCORE_GEWICHTE_JE_ZIEL[ziel].values()) == 100, ziel


def test_tofu_gewichtet_die_hook_am_staerksten():
    tofu = SCORE_GEWICHTE_JE_ZIEL["TOFU"]
    mofu = SCORE_GEWICHTE_JE_ZIEL["MOFU"]
    hook_tofu = tofu["sprech_hook"] + tofu["text_hook"] + tofu["visuell_hook"]
    hook_mofu = mofu["sprech_hook"] + mofu["text_hook"] + mofu["visuell_hook"]
    assert hook_tofu > hook_mofu
    assert tofu["visuell_hook"] > mofu["visuell_hook"]
    assert mofu["spannungsbogen"] > tofu["spannungsbogen"]
    assert tofu["visuelle_aesthetik"] > mofu["visuelle_aesthetik"]


def test_cta_nur_bei_bofu_gewichtet():
    assert SCORE_GEWICHTE_JE_ZIEL["TOFU"]["cta"] == 0
    assert SCORE_GEWICHTE_JE_ZIEL["MOFU"]["cta"] == 0
    assert SCORE_GEWICHTE_JE_ZIEL["BOFU"]["cta"] > 0


def test_alle_ziele_kennen_dieselben_dimensionen():
    schluessel = [set(SCORE_GEWICHTE_JE_ZIEL[z]) for z in ZIELE]
    assert schluessel[0] == schluessel[1] == schluessel[2]
