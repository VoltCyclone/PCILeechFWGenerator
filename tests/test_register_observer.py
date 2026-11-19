"""Hardware-free fixtures in the exact REGISTER_OBSERVER_V1 CSV format."""

from pcileechfwgenerator.device_clone.register_evidence import RegisterClassification
from pcileechfwgenerator.host_collect.register_observer import (
    BarResource,
    parse_trace_records,
)

BARS = [BarResource(bar_index=2, physical_start=0x80000000, size=4096)]
VADDR = 0xFFFFC90000100000


def parse(text, bars=BARS):
    return parse_trace_records(text.splitlines(keepends=True), bars, state_epoch=3)


def test_mapping_is_required_never_subtract_physical_from_pointer():
    result = parse(
        "write,123,10,4,0xffffc90000100010,85,,,,\n"
        "write,123,11,4,0x80000010,85,,,,\n"
    )
    assert result.observations == []
    assert result.unattributed_accesses == result.dropped_events == 2


def test_real_format_read_write_and_partial_mapping():
    result = parse(
        "REGISTER_OBSERVER_V1\n"
        "map,123,10,,0xffffc90000100000,,0x80000100,256,,\n"
        "read_enter,123,20,4,0xffffc90000100010,,,,20,\n"
        "read_return,123,28,4,0xffffc90000100010,85,,,20,ok\n"
        "write,123,30,2,0xffffc90000100012,65535,,,,\n"
    )
    read, write = result.observations
    assert (read.ref.bar_index, read.ref.offset, read.ref.width) == (2, 0x110, 4)
    assert read.returned_value == 85 and read.duration_ns == 8
    assert write.ref.offset == 0x112 and write.attempted_value == 65535
    assert write.returned_value is None and write.duration_ns is None
    assert all(
        o.source == "native_driver" and o.mediation == "unknown" and o.state_epoch == 3
        for o in result.observations
    )
    assert [o.sequence_id for o in result.observations] == [0, 1]
    assert result.dropped_events == 0
    # Passive observations do not establish any permissions.
    cls = RegisterClassification(ref=read.ref, observed_initial_value=85)
    assert cls.unknown_mask() == 0xFFFFFFFF and cls.mask_for("rw") == 0


def test_interleaved_threads_and_nested_calls():
    result = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "read_enter,123,20,4,0xffffc90000100010,,,,20,\n"
        "read_enter,456,21,1,0xffffc90000100020,,,,21,\n"
        "read_enter,123,22,2,0xffffc90000100030,,,,22,\n"
        "read_return,456,23,1,0xffffc90000100020,128,,,21,ok\n"
        "read_return,123,24,2,0xffffc90000100030,32768,,,22,ok\n"
        "read_return,123,25,4,0xffffc90000100010,2147483648,,,20,ok\n"
    )
    assert [
        (o.ref.offset, o.returned_value, o.duration_ns) for o in result.observations
    ] == [(0x20, 128, 2), (0x30, 32768, 2), (0x10, 2147483648, 5)]
    assert result.dropped_events == 0


def test_unmap_reuse_by_other_device_does_not_retain_target_mapping():
    result = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "write,123,11,4,0xffffc90000100010,85,,,,\n"
        "unmap,123,12,,0xffffc90000100000,,,,,\n"
        "write,123,13,4,0xffffc90000100010,86,,,,\n"
        "map,456,14,,0xffffc90000100000,,0x90000000,4096,,\n"
        "write,456,15,4,0xffffc90000100010,87,,,,\n"
    )
    assert [o.attempted_value for o in result.observations] == [85]
    assert result.unattributed_accesses == 2


def test_replacement_mapping_without_unmap_retires_pending_read():
    result = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "read_enter,123,11,4,0xffffc90000100010,,,,11,\n"
        "map,456,12,,0xffffc90000100000,,0x90000000,4096,,\n"
        "read_return,123,13,4,0xffffc90000100010,85,,,11,ok\n"
    )
    assert result.observations[0].returned_value is None
    assert "mapping retired" in result.observations[0].absent_reason
    assert result.unmatched_reads == 1


def test_target_bars_are_distinct_and_unrelated_traffic_is_excluded():
    result = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "map,123,11,,0xffffc90000200000,,0x81000000,4096,,\n"
        "map,456,12,,0xffffc90000300000,,0x90000000,4096,,\n"
        "write,123,13,4,0xffffc90000100010,1,,,,\n"
        "write,123,14,4,0xffffc90000200020,2,,,,\n"
        "read_enter,456,15,4,0xffffc90000300010,,,,15,\n"
        "read_return,456,16,4,0xffffc90000300010,3,,,15,ok\n",
        BARS + [BarResource(bar_index=4, physical_start=0x81000000, size=4096)],
    )
    assert [(o.ref.bar_index, o.ref.offset) for o in result.observations] == [
        (2, 16),
        (4, 32),
    ]
    assert result.unattributed_accesses == 1


def test_unmatched_and_failed_reads_never_invent_zero():
    result = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "read_enter,123,11,4,0xffffc90000100010,,,,11,\n"
        "read_return,123,12,4,0xffffc90000100010,,,,11,error\n"
        "read_return,456,13,4,0xffffc90000100010,85,,,9,ok\n"
        "read_enter,123,14,4,0xffffc90000100020,,,,14,\n"
        "read_enter,456,15,4,0xffffc90000200000,,,,15,\n"
    )
    assert len(result.observations) == 2
    assert all(
        o.returned_value is None and o.absent_reason and o.completion == "error"
        for o in result.observations
    )
    assert result.unmatched_reads == 3 and result.unattributed_accesses == 1


def test_losses_and_truncation_invalidate_mappings_and_pending_reads():
    result = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "read_enter,123,11,4,0xffffc90000100010,,,,11,\n"
        "lost,0,12,,,7,,,,\n"
        "write,123,13,4,0xffffc90000100010,85,,,,\n"
        "map,123,14,,0xffffc90000100000,,0x80000000,4096,,\n"
        "Lost 2 events\n"
        "write,123,15,4,0xffffc90000100010,85,,,,\n"
        "write,123,16,4,0xffffc90000100010,85,,,,"
    )
    assert result.lost_events == 9
    assert result.malformed_lines == 1
    assert result.unmatched_reads == 1 and result.unattributed_accesses == 2
    assert result.dropped_events == 13
    assert len(result.observations) == 1
    assert result.observations[0].returned_value is None


def test_out_of_order_event_cannot_reintroduce_stale_mapping():
    result = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "unmap,123,20,,0xffffc90000100000,,,,,\n"
        "map,123,15,,0xffffc90000100000,,0x80000000,4096,,\n"
        "write,123,21,4,0xffffc90000100010,85,,,,\n"
    )
    assert result.observations == []
    assert result.out_of_order_events == 1 and result.unattributed_accesses == 1


def test_full_bounds_alignment_and_ambiguous_resources():
    result = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "write,123,11,4,0xffffc90000100ffe,85,,,,\n"
        "write,123,12,4,0xffffc90000100011,85,,,,\n"
    )
    assert result.observations == [] and result.unattributed_accesses == 2
    ambiguous = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "write,123,11,4,0xffffc90000100010,85,,,,\n",
        BARS + BARS,
    )
    assert ambiguous.observations == []


def test_malformed_records_are_counted_and_empty_trace_has_no_evidence():
    assert parse("").observations == []
    result = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "write,123,11,1,0xffffc90000100010,256,,,,\n"
        "not,a,trace\n"
        "read_enter,123,12,3,0xffffc90000100010,,,,12,\n"
    )
    assert result.observations == [] and result.malformed_lines == 3


def test_delayed_unmap_retracts_prior_potentially_stale_observations():
    result = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "write,123,30,4,0xffffc90000100010,85,,,,\n"
        "unmap,456,20,,0xffffc90000100000,,,,,\n"
        "write,123,31,4,0xffffc90000100010,86,,,,\n"
    )
    assert result.observations == []
    assert result.out_of_order_events == 1
    assert result.discarded_observations == 1
    assert result.unattributed_accesses == 1


def test_duplicate_read_ids_are_ambiguous_even_when_address_matches():
    result = parse(
        "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
        "read_enter,123,11,4,0xffffc90000100010,,,,11,\n"
        "read_enter,123,12,4,0xffffc90000100010,,,,11,\n"
        "read_return,123,13,4,0xffffc90000100010,85,,,11,ok\n"
    )
    assert len(result.observations) == 2
    assert all(
        o.returned_value is None and o.absent_reason == "duplicate read entry"
        for o in result.observations
    )
    assert result.unmatched_reads == 3
