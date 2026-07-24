"""Test risk manager."""

import pytest
from pocket_option_bot.core.risk_manager import RiskManager

def test_initial_stake():
    rm = RiskManager()
    assert rm.get_stake() == 1.0

def test_loss_multiplier():
    rm = RiskManager()
    rm.apply_loss(1.0)
    assert rm.get_stake() == 2.5
    assert rm.get_consecutive_losses() == 1

def test_win_reset():
    rm = RiskManager()
    rm.apply_loss(1.0)
    rm.apply_win(2.5)
    assert rm.get_stake() == 1.0
    assert rm.get_consecutive_losses() == 0

def test_max_losses_stop():
    rm = RiskManager()
    for _ in range(5):
        stop = rm.apply_loss(1.0)
    assert stop is True