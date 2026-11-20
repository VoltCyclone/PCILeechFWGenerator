"""VFIOBinder acquisition is transactional and restoration is verified.

A stateful fake models the kernel's binding state (driver, driver_override,
vfio-pci dynamic IDs) at the binder's sysfs primitives, so failures can be
injected after each acquisition step without hardware.
"""

import pytest
from pcileechfwgenerator.cli.vfio_handler import VFIOBinder
from pcileechfwgenerator.exceptions import VFIOBindError

BDF = "0000:03:00.0"


class Kernel:
    def __init__(self, driver="e1000e", override=""):
        self.driver = driver
        self.native = driver if driver != "vfio-pci" else "e1000e"
        self.override = override
        self.vfio_ids = False
        self.locked = False
        self.fail = set()

    def check(self, step):
        if step in self.fail:
            raise OSError(f"injected failure: {step}")


def binder_for(kernel, monkeypatch, tmp_path=None):
    monkeypatch.setattr(VFIOBinder, "_validate_permissions", staticmethod(lambda: None))
    monkeypatch.setattr(VFIOBinder, "_check_security_context", lambda self: None)
    binder = VFIOBinder(
        BDF, attach=False, journal_directory=tmp_path / "j" if tmp_path else None
    )
    k = kernel

    def bind_driver(driver):
        k.check("bind:" + driver)
        if k.driver is not None:
            raise OSError("device already has a driver")
        if k.override and k.override != driver:
            raise OSError("driver_override forbids " + driver)
        k.driver = driver

    def probe():
        k.check("probe")
        if k.driver is None:
            k.driver = k.override or ("vfio-pci" if k.vfio_ids else k.native)

    def unbind_current():
        k.check("unbind")
        k.driver = None

    def set_override():
        k.check("override")
        k.override = "vfio-pci"

    def bind_vfio():
        k.vfio_ids = True
        binder._added_device_ids.append(("8086", "1533"))
        k.check("bind_vfio")
        k.driver = "vfio-pci"
        binder._bound = True

    def wait_group(_group):
        k.check("group_node")

    def cleanup_ids():
        if "remove_id" in k.fail:
            return ["remove_id 8086:1533 failed: injected"]
        k.vfio_ids = False
        binder._added_device_ids.clear()
        return []

    def save_original():
        binder.original_driver = k.driver

    def acquire(_group):
        assert not k.locked
        k.locked = True

    def release():
        k.locked = False

    def write_override(value):
        k.check("restore_override")
        k.override = value

    patches = {
        "_check_iommu": lambda: None,
        "_get_iommu_group": lambda: "7",
        "_acquire_group_lock": acquire,
        "_release_group_lock": release,
        "_save_original_driver": save_original,
        "_unbind_current_driver": unbind_current,
        "_set_driver_override": set_override,
        "_bind_to_vfio": bind_vfio,
        "_wait_for_group_node": wait_group,
        "_cleanup_device_ids": cleanup_ids,
        "_current_driver": lambda: k.driver,
        "_read_override": lambda: k.override,
        "_write_override": write_override,
        "_unbind_driver": lambda: setattr(k, "driver", None),
        "_bind_driver": bind_driver,
        "_probe_driver": probe,
    }
    for name, fn in patches.items():
        monkeypatch.setattr(binder, name, fn)
    return binder


def test_round_trip_restores_and_verifies(monkeypatch, tmp_path):
    k = Kernel()
    binder = binder_for(k, monkeypatch, tmp_path)
    with binder:
        assert k.driver == "vfio-pci" and k.locked
        assert (tmp_path / "j").exists() and any((tmp_path / "j").iterdir())
    assert (k.driver, k.override, k.vfio_ids, k.locked) == ("e1000e", "", False, False)
    assert binder.final_driver == "e1000e"
    assert not any((tmp_path / "j").iterdir())  # journal cleared after verify


@pytest.mark.parametrize("step", ["unbind", "override", "bind_vfio", "group_node"])
def test_failure_after_each_step_rolls_back(monkeypatch, tmp_path, step):
    """The review's scenario: bind() fails after changing the binding; __exit__
    never runs, so bind() itself must restore the original driver."""
    k = Kernel()
    k.fail.add(step)
    binder = binder_for(k, monkeypatch, tmp_path)
    with pytest.raises(Exception, match="injected failure"):
        with binder:
            pytest.fail("body must not run")
    assert (k.driver, k.override, k.vfio_ids, k.locked) == ("e1000e", "", False, False)
    assert not any((tmp_path / "j").iterdir())


def test_failed_rollback_reports_final_binding_and_keeps_journal(monkeypatch, tmp_path):
    k = Kernel()
    k.fail |= {"group_node", "bind:e1000e", "probe"}
    binder = binder_for(k, monkeypatch, tmp_path)
    with pytest.raises(VFIOBindError) as caught:
        binder.bind()
    message = str(caught.value)
    assert "rollback failed" in message and "driver is none" in message
    assert binder.final_driver is None
    assert not k.locked  # lock released after the recovery attempt
    # The next session refuses to touch the device until someone checks it.
    k.fail.clear()
    again = binder_for(k, monkeypatch, tmp_path)
    with pytest.raises(VFIOBindError, match="did not confirm"):
        again.bind()
    assert k.driver is None and not k.locked


def test_preexisting_vfio_is_used_in_place_and_left_bound(monkeypatch, tmp_path):
    """The review's second scenario: a donor already on vfio-pci used to end
    up with no driver at all."""
    k = Kernel(driver="vfio-pci", override="vfio-pci")
    binder = binder_for(k, monkeypatch, tmp_path)
    with binder:
        assert k.driver == "vfio-pci"
    assert (k.driver, k.override, k.locked) == ("vfio-pci", "vfio-pci", False)
    assert binder.final_driver == "vfio-pci"


def test_unbind_failure_is_raised_with_actual_binding(monkeypatch, tmp_path):
    k = Kernel()
    binder = binder_for(k, monkeypatch, tmp_path)
    binder.bind()
    k.fail |= {"bind:e1000e", "probe"}
    with pytest.raises(VFIOBindError, match="driver is none"):
        binder.unbind()
    assert not k.locked
    assert any((tmp_path / "j").iterdir())  # incident survives for next session


def test_foreign_driver_is_not_touched(monkeypatch, tmp_path):
    k = Kernel()
    binder = binder_for(k, monkeypatch, tmp_path)
    binder.bind()
    k.driver = "nouveau"  # someone else rebound it meanwhile
    with pytest.raises(VFIOBindError, match="left untouched"):
        binder.unbind()
    assert k.driver == "nouveau"


def test_override_restore_failure_is_reported(monkeypatch, tmp_path):
    k = Kernel()
    binder = binder_for(k, monkeypatch, tmp_path)
    binder.bind()
    k.fail.add("restore_override")
    with pytest.raises(VFIOBindError, match="driver_override was not restored"):
        binder.unbind()
    assert not k.locked


def test_without_journal_directory_no_files_are_written(monkeypatch, tmp_path):
    k = Kernel()
    binder = binder_for(k, monkeypatch)
    with binder:
        pass
    assert k.driver == "e1000e"


def test_mismatched_original_override_is_restored(monkeypatch, tmp_path):
    """driver X with override Y. X must be bound with the override
    cleared, then Y put back; restoring Y first makes the kernel refuse X."""
    k = Kernel(driver="e1000e", override="vfio-pci")
    binder = binder_for(k, monkeypatch, tmp_path)
    with binder:
        assert k.driver == "vfio-pci"
    assert (k.driver, k.override) == ("e1000e", "vfio-pci")


def test_preexisting_vfio_with_empty_override_is_not_given_one(monkeypatch, tmp_path):
    """a device on vfio-pci via ids= has no override; restore must not
    invent one (which then fails its own verification)."""
    k = Kernel(driver="vfio-pci", override="")
    binder = binder_for(k, monkeypatch, tmp_path)
    binder.bind()
    k.driver = None  # e.g. something unbound it during the session
    binder.unbind()
    assert (k.driver, k.override) == ("vfio-pci", "")


def test_leftover_dynamic_id_is_reported_when_binding_fails(monkeypatch, tmp_path):
    """a failed remove_id lets drivers_probe hand the device back to
    vfio-pci; the error must say why."""
    k = Kernel()
    binder = binder_for(k, monkeypatch, tmp_path)
    binder.bind()
    k.fail |= {"remove_id", "bind:e1000e"}
    with pytest.raises(VFIOBindError) as caught:
        binder.unbind()
    assert "driver is vfio-pci" in str(caught.value)
    assert "remove_id 8086:1533 failed" in str(caught.value)


def test_permission_error_does_not_skip_remaining_steps(monkeypatch, tmp_path):
    """every restore step is attempted and the result verified."""
    from pcileechfwgenerator.exceptions import VFIOPermissionError

    k = Kernel()
    binder = binder_for(k, monkeypatch, tmp_path)
    binder.bind()
    unbind = binder._unbind_driver

    def denied():
        raise VFIOPermissionError("denied")

    monkeypatch.setattr(binder, "_unbind_driver", denied)
    with pytest.raises(VFIOBindError, match="unbind vfio-pci: denied"):
        binder.unbind()
    assert binder.final_driver == "vfio-pci"  # verified and reported
    monkeypatch.setattr(binder, "_unbind_driver", unbind)


def test_rebinding_a_reused_binder_does_not_restore_a_stale_driver(
    monkeypatch, tmp_path
):
    """original_driver is re-read on every bind(); a device with no
    driver now must not inherit the previous session's driver."""
    from types import SimpleNamespace

    monkeypatch.setattr(VFIOBinder, "_validate_permissions", staticmethod(lambda: None))
    monkeypatch.setattr(VFIOBinder, "_check_security_context", lambda self: None)
    binder = VFIOBinder(BDF, attach=False, journal_directory=None)
    binder.original_driver = "e1000e"  # from an earlier bind()
    binder._path_manager = SimpleNamespace(driver_path=tmp_path / "no-driver")
    binder._save_original_driver()
    assert binder.original_driver is None


def test_every_binder_journals_by_default(monkeypatch, tmp_path):
    """ConfigSpaceManager's binder (no explicit directory) journals too."""
    from pcileechfwgenerator.host_collect.register_learning import JOURNAL_DIR_ENV

    monkeypatch.setenv(JOURNAL_DIR_ENV, str(tmp_path / "host"))
    monkeypatch.setattr(VFIOBinder, "_validate_permissions", staticmethod(lambda: None))
    monkeypatch.setattr(VFIOBinder, "_check_security_context", lambda self: None)
    binder = VFIOBinder(BDF, attach=False)
    assert binder._journal_directory == tmp_path / "host" / "bindings"
    assert (
        VFIOBinder(BDF, attach=False, journal_directory=None)._journal_directory is None
    )
