"""
Tests for ThrottleConfig — validation, defaults, env vars.
"""

import os

import pytest
from pydantic import ValidationError

from fastapi_gradual_throttle import defaults
from fastapi_gradual_throttle.config import ThrottleConfig


class TestDefaults:
    def test_default_values(self):
        cfg = ThrottleConfig()
        assert cfg.rate == defaults.DEFAULT_RATE
        assert cfg.window == defaults.DEFAULT_WINDOW
        assert cfg.base_delay == defaults.DEFAULT_BASE_DELAY
        assert cfg.max_delay == defaults.DEFAULT_MAX_DELAY
        assert cfg.mode == "gradual"
        assert cfg.enabled is True
        assert cfg.dry_run is False
        assert cfg.headers_enabled is True
        assert cfg.fail_open is True
        assert cfg.hard_limit == 0
        assert cfg.window_type == "fixed"
        assert cfg.key_prefix == "throttle"
        assert cfg.exempt_paths == []
        assert cfg.trusted_proxies == []

    def test_kwarg_override(self):
        cfg = ThrottleConfig(rate=200, window=120, mode="strict")
        assert cfg.rate == 200
        assert cfg.window == 120
        assert cfg.mode == "strict"


class TestValidation:
    def test_rate_must_be_positive(self):
        with pytest.raises(ValidationError, match="rate must be > 0"):
            ThrottleConfig(rate=0)

    def test_negative_rate(self):
        with pytest.raises(ValidationError, match="rate must be > 0"):
            ThrottleConfig(rate=-1)

    def test_window_must_be_positive(self):
        with pytest.raises(ValidationError, match="window must be > 0"):
            ThrottleConfig(window=0)

    def test_base_delay_non_negative(self):
        with pytest.raises(ValidationError, match="base_delay must be >= 0"):
            ThrottleConfig(base_delay=-0.1)

    def test_max_delay_non_negative(self):
        with pytest.raises(ValidationError, match="max_delay must be >= 0"):
            ThrottleConfig(max_delay=-1.0, base_delay=0.0)

    def test_max_delay_gte_base_delay(self):
        with pytest.raises(ValidationError, match="max_delay.*must be >= base_delay"):
            ThrottleConfig(base_delay=5.0, max_delay=1.0)

    def test_hard_limit_non_negative(self):
        with pytest.raises(ValidationError, match="hard_limit must be >= 0"):
            ThrottleConfig(hard_limit=-1)

    def test_hard_limit_less_than_rate(self):
        with pytest.raises(ValidationError, match="hard_limit.*must be >= rate"):
            ThrottleConfig(rate=100, hard_limit=50)

    def test_hard_limit_zero_is_valid(self):
        cfg = ThrottleConfig(rate=100, hard_limit=0)
        assert cfg.hard_limit == 0

    def test_hard_limit_equal_to_rate_is_valid(self):
        cfg = ThrottleConfig(rate=100, hard_limit=100)
        assert cfg.hard_limit == 100

    def test_key_prefix_not_empty(self):
        with pytest.raises(ValidationError, match="key_prefix must not be empty"):
            ThrottleConfig(key_prefix="   ")

    def test_key_prefix_stripped(self):
        cfg = ThrottleConfig(key_prefix="  myapp  ")
        assert cfg.key_prefix == "myapp"

    def test_mode_invalid(self):
        with pytest.raises(ValidationError):
            ThrottleConfig(mode="invalid")

    def test_window_type_invalid(self):
        with pytest.raises(ValidationError):
            ThrottleConfig(window_type="invalid")


class TestEnvVars:
    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("FASTAPI_GRADUAL_THROTTLE_RATE", "200")
        monkeypatch.setenv("FASTAPI_GRADUAL_THROTTLE_WINDOW", "120")
        monkeypatch.setenv("FASTAPI_GRADUAL_THROTTLE_MODE", "strict")
        cfg = ThrottleConfig()
        assert cfg.rate == 200
        assert cfg.window == 120
        assert cfg.mode == "strict"

    def test_kwarg_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("FASTAPI_GRADUAL_THROTTLE_RATE", "200")
        cfg = ThrottleConfig(rate=50)
        assert cfg.rate == 50


class TestAdditionalValidation:
    def test_burst_size_negative(self):
        with pytest.raises(ValidationError, match="burst_size must be >= 0"):
            ThrottleConfig(burst_size=-1)

    def test_trusted_proxies_invalid_cidr(self):
        with pytest.raises(ValidationError, match="Invalid CIDR"):
            ThrottleConfig(trusted_proxies=["not-a-cidr"])

    def test_trusted_proxies_valid(self):
        cfg = ThrottleConfig(trusted_proxies=["10.0.0.0/8", "192.168.1.1"])
        assert cfg.trusted_proxies == ["10.0.0.0/8", "192.168.1.1"]

    def test_strict_mode_with_hard_limit_raises(self):
        with pytest.raises(
            ValidationError, match="hard_limit has no effect when mode='strict'"
        ):
            ThrottleConfig(mode="strict", hard_limit=100, rate=10)

    def test_token_bucket_requires_burst_size(self):
        with pytest.raises(ValidationError, match="burst_size must be > 0"):
            ThrottleConfig(window_type="token_bucket", burst_size=0)

    def test_token_bucket_with_burst_size_valid(self):
        cfg = ThrottleConfig(window_type="token_bucket", burst_size=10)
        assert cfg.window_type == "token_bucket"
        assert cfg.burst_size == 10

    def test_redis_backend_default_prefix_warns(self):
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ThrottleConfig(
                backend="fastapi_gradual_throttle.backends.redis.RedisBackend"
            )
            prefix_warnings = [x for x in w if "key_prefix" in str(x.message)]
            assert len(prefix_warnings) == 1

    def test_redis_backend_custom_prefix_no_warn(self):
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ThrottleConfig(
                backend="fastapi_gradual_throttle.backends.redis.RedisBackend",
                key_prefix="myapp",
            )
            prefix_warnings = [x for x in w if "key_prefix" in str(x.message)]
            assert len(prefix_warnings) == 0

    def test_all_modes_valid(self):
        import warnings

        for mode in ("gradual", "strict"):
            cfg = ThrottleConfig(mode=mode)
            assert cfg.mode == mode
        # combined without hard_limit is valid but emits a UserWarning
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = ThrottleConfig(mode="combined")
            assert cfg.mode == "combined"
            assert any("hard_limit" in str(warning.message) for warning in w)
        # combined with hard_limit set should produce no warning
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = ThrottleConfig(mode="combined", hard_limit=200)
            assert cfg.mode == "combined"
            assert not any("hard_limit" in str(warning.message) for warning in w)

    def test_all_window_types_valid(self):
        for wt in ("fixed", "sliding"):
            cfg = ThrottleConfig(window_type=wt)
            assert cfg.window_type == wt
        cfg = ThrottleConfig(window_type="token_bucket", burst_size=5)
        assert cfg.window_type == "token_bucket"
