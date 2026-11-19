"""Conformance checks for the register-access boundary, without hardware."""

import pytest
from pcileechfwgenerator.device_clone.register_evidence import RegisterRef
from pcileechfwgenerator.host_collect.register_access import (
    AccessResult,
    AccessValidationError,
    RegisterAccessBackend,
    RegisterRegion,
    validate_access,
)
from pydantic import ValidationError

from tests.register_fakes import FakeRegisterDevice


@pytest.mark.parametrize("space,bar", [("config", None), ("mem_bar", 0), ("io_bar", 5)])
def test_explicit_region_and_width(space, bar):
    ref = RegisterRef(space=space, bar_index=bar, offset=0, width=4, field_mask=1)
    device = FakeRegisterDevice(ref=ref)
    assert isinstance(device, RegisterAccessBackend)
    assert validate_access(device.discover_regions(), ref, "read") == device.region
    assert device.write(ref, 1, 15).completion == "ok"
    assert device.read(ref).value == 1


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"offset": 1}, "misaligned"),
        ({"width": 8}, "unsupported_width"),
        ({"width": 2}, "unsupported_width"),
        ({"offset": 8}, "out_of_bounds"),
        ({"offset": -4}, "out_of_bounds"),
        ({"bar_index": 1}, "region_missing"),
    ],
)
def test_rejects_before_io(changes, code):
    device = FakeRegisterDevice()
    # model_copy deliberately bypasses RegisterRef's own validation to exercise
    # the backend's defense at the I/O boundary.
    ref = device.ref.model_copy(update=changes)
    with pytest.raises(AccessValidationError) as caught:
        device.read(ref)
    assert caught.value.rejection.code == code
    assert device.operations == []


@pytest.mark.parametrize(
    "value,lanes,code",
    [
        (-1, 15, "invalid_value"),
        (1 << 32, 15, "invalid_value"),
        (True, 15, "invalid_value"),
        (0, 0, "invalid_byte_enable"),
        (0, 16, "invalid_byte_enable"),
        (0, True, "invalid_byte_enable"),
    ],
)
def test_invalid_write_request_has_structured_rejection(value, lanes, code):
    device = FakeRegisterDevice()
    with pytest.raises(AccessValidationError) as caught:
        device.write(device.ref, value, lanes)
    assert caught.value.rejection.code == code
    assert caught.value.rejection.operation == "write"
    assert device.operations == []


def test_backend_permission_is_distinct_from_device_read_only():
    rejected = FakeRegisterDevice(writable=False)
    with pytest.raises(AccessValidationError, match="not_writable"):
        rejected.write(rejected.ref, 1, 15)
    assert not rejected.operations
    ignored = FakeRegisterDevice(rw_mask=0)
    assert ignored.write(ignored.ref, 1, 15).completion == "ok"
    assert ignored.read(ignored.ref).value == 0


def test_byte_enable_does_not_modify_other_lanes():
    device = FakeRegisterDevice(value=0x12345678, rw_mask=0xFFFFFFFF)
    device.write(device.ref, 0xABCDEF00, 0b0101)
    assert device.read(device.ref).value == 0x12CD5600


def test_64_bit_bar_access_is_one_explicit_operation():
    ref = RegisterRef(
        space="mem_bar", bar_index=0, offset=8, width=8, field_mask=1 << 63
    )
    device = FakeRegisterDevice(ref=ref, rw_mask=ref.full_mask)
    device.write(ref, 0x123456789ABCDEF0, 255)
    assert device.read(ref).value == 0x123456789ABCDEF0
    assert len(device.operations) == 2
    assert all(item[1].width == 8 for item in device.operations)


@pytest.mark.parametrize(
    "fields",
    [
        {"completion": "ok", "value": None},
        {"completion": "error", "value": 0},
        {"completion": "error", "reason": ""},
        {"completion": "ok", "value": 1, "duration_ns": -1},
    ],
)
def test_invalid_access_results_rejected(fields):
    with pytest.raises(ValidationError):
        AccessResult(mediation="unknown", **fields)


def test_unknown_duration_stays_unknown():
    result = AccessResult(completion="ok", value=0, mediation="mediated")
    assert result.duration_ns is None


def test_ambiguous_and_unreadable_region_rejected():
    device = FakeRegisterDevice()
    with pytest.raises(AccessValidationError, match="region_ambiguous"):
        validate_access((device.region, device.region), device.ref, "read")
    region = device.region.model_copy(update={"readable": False})
    with pytest.raises(AccessValidationError, match="not_readable"):
        validate_access((region,), device.ref, "read")


@pytest.mark.parametrize(
    "fields",
    [
        {"space": "config", "bar_index": 0},
        {"space": "mem_bar", "bar_index": None},
        {"permitted_widths": (3,)},
        {"permitted_widths": (4, 4)},
        {"space": "config", "bar_index": None, "permitted_widths": (8,)},
        {"space": "config", "bar_index": None, "size": 4097},
    ],
)
def test_region_validation(fields):
    data = dict(
        space="mem_bar",
        bar_index=0,
        size=8,
        permitted_widths=(4,),
        readable=True,
        writable=True,
    )
    data.update(fields)
    with pytest.raises(ValidationError):
        RegisterRegion(**data)
