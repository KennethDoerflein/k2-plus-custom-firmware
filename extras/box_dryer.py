# Copyright (C) 2026 Kenneth Doerflein
# This file may be distributed under the terms of the GNU GPLv3 license.
"""CFS Pro Heated Dryer Mixin and Protocol Constants."""

import logging

DRYER_FLAG_AC_CONNECTED = 0x01
DRYER_FLAG_CH0_HEATING = 0x04
DRYER_FLAG_CH1_HEATING = 0x08

DRYER_CH0 = 0
DRYER_CH1 = 1
DRYER_CH_BOTH = 2

SLOTS_PER_BOX = 4


def _dlog(msg, *args, level=logging.info):
    level("box_dryer: " + msg, *args)


class BoxDryerMixin:
    """CFS Pro Heated Dryer command handlers, telemetry and automation."""

    def _init_dryer(self):
        self.dryer_auto_enabled = False
        self.auto_humidity_enabled = False
        self.auto_humidity_threshold = 25
        self.dryer_last_targets = {}
        self.printer.register_event_handler(
            "print_stats:complete_printing", self._on_print_complete_dry)

    def _dryer_commands(self):
        return (
            ("BOX_SET_DRY_MODE", self.cmd_set_dry_mode,
             "Configure and start CFS dryer heating"),
            ("_BOX_SET_DRY_MODE", self.cmd_set_dry_mode,
             "Configure and start CFS dryer heating"),
            ("BOX_GET_DRY_MODE", self.cmd_get_dry_mode,
             "Query CFS dryer telemetry and state"),
            ("BOX_PAUSE_DRY", self.cmd_pause_dry,
             "Pause CFS dryer heating"),
            ("_BOX_PAUSE_DRY", self.cmd_pause_dry,
             "Pause CFS dryer heating"),
            ("CONTINUE_PAUSE_DRY", self.cmd_continue_dry,
             "Resume paused CFS dryer heating"),
            ("_CONTINUE_PAUSE_DRY", self.cmd_continue_dry,
             "Resume paused CFS dryer heating"),
            ("BOX_SET_AUTO_DRY_MODE", self.cmd_set_auto_dry,
             "Set automatic drying after print"),
            ("_BOX_SET_AUTO_DRY_MODE", self.cmd_set_auto_dry,
             "Set automatic drying after print"),
            ("BOX_SET_AUTO_HUMIDITY_MODE", self.cmd_set_auto_humidity,
             "Set automatic humidity maintenance"),
            ("_BOX_SET_AUTO_HUMIDITY_MODE", self.cmd_set_auto_humidity,
             "Set automatic humidity maintenance"),
        )

    def _dryer_status(self, snap):
        boxes = {}
        for addr in sorted(self.drivers):
            reply = self.box_replies.get(addr)
            boxes[str(addr)] = {
                "supported": bool(getattr(reply, "dryer_supported", False)),
                "ac_connected": bool(getattr(reply, "ac_connected", False)),
                "ch0": {
                    "heating": bool(getattr(reply, "ch0_heating", False)),
                    "target_temp": int(getattr(reply, "ch0_target_temp", 0)),
                    "remaining_time": int(getattr(reply, "ch0_remaining_time", 0)),
                    "cur_temp": int(getattr(reply, "ch0_cur_temp", 0)),
                },
                "ch1": {
                    "heating": bool(getattr(reply, "ch1_heating", False)),
                    "target_temp": int(getattr(reply, "ch1_target_temp", 0)),
                    "remaining_time": int(getattr(reply, "ch1_remaining_time", 0)),
                    "cur_temp": int(getattr(reply, "ch1_cur_temp", 0)),
                },
            }
        return {
            "supported": snap.dryer_supported,
            "ac_connected": snap.ac_connected,
            "auto_dry_enabled": self.dryer_auto_enabled,
            "auto_humidity_enabled": self.auto_humidity_enabled,
            "auto_humidity_threshold": self.auto_humidity_threshold,
            "ch0": {
                "heating": snap.ch0_heating,
                "target_temp": snap.ch0_target_temp,
                "remaining_time": snap.ch0_remaining_time,
                "cur_temp": snap.ch0_cur_temp,
            },
            "ch1": {
                "heating": snap.ch1_heating,
                "target_temp": snap.ch1_target_temp,
                "remaining_time": snap.ch1_remaining_time,
                "cur_temp": snap.ch1_cur_temp,
            },
            "boxes": boxes,
        }

    def cmd_set_dry_mode(self, gcmd):
        box_addr = gcmd.get_int("BOX", None)
        if box_addr is None:
            box_addr = gcmd.get_int("ADDR", 1)
        driver = self.drivers.get(box_addr)
        if driver is None:
            raise gcmd.error("[BOX]: CFS box %d is not online" % box_addr)

        params = gcmd.get_command_parameters()

        # Chamber / channel selection:
        # Channels: 0 = Left (slots 0,1), 1 = Right (slots 2,3), 2 = Both
        # Mask: 0x01 = Left, 0x02 = Right, 0x03 = Both
        if "SLOT" in params:
            slot = gcmd.get_int("SLOT", 0)
            channel_mask = 1 if (slot % SLOTS_PER_BOX) < 2 else 2
        elif "CH" in params or "CHANNEL" in params:
            ch_val = params.get("CH") if "CH" in params else params.get("CHANNEL")
            ch_raw = str(ch_val).strip().upper()
            if ch_raw in ("0", "LEFT", "L", "CH0"):
                channel_mask = 1
            elif ch_raw in ("1", "RIGHT", "R", "CH1"):
                channel_mask = 2
            elif ch_raw in ("2", "BOTH", "ALL", "CH2"):
                channel_mask = 3
            else:
                try:
                    c_int = int(ch_raw)
                    channel_mask = 1 if c_int == 0 else (2 if c_int == 1 else 3)
                except ValueError:
                    raise gcmd.error(
                        "[BOX]: Invalid CH/CHANNEL '%s' (use 0, 1, or 2 for Both)"
                        % ch_raw)
        elif "BIN" in params or "KEEP_DRY_BIN_CHOICE" in params:
            bin_val = (
                params.get("BIN") if "BIN" in params
                else params.get("KEEP_DRY_BIN_CHOICE"))
            bin_raw = str(bin_val).strip().upper()
            if bin_raw in ("0", "LEFT"):
                channel_mask = 1
            elif bin_raw in ("1", "RIGHT"):
                channel_mask = 2
            elif bin_raw in ("2", "3", "BOTH", "ALL"):
                channel_mask = 3
            else:
                try:
                    b_val = int(bin_raw)
                    channel_mask = b_val if 1 <= b_val <= 3 else (1 if b_val == 0 else 3)
                except ValueError:
                    channel_mask = 3
        else:
            channel_mask = 3

        target_temp = gcmd.get_int("TARGET_TEMP", None)
        if target_temp is None:
            target_temp = gcmd.get_int("TEMP", 50)
        if not 30 <= target_temp <= 75:
            raise gcmd.error(
                "[BOX]: TARGET_TEMP %dC out of range (30..75C)" % target_temp)

        duration_minutes = gcmd.get_int("TOTAL_TIME", None)
        if duration_minutes is None:
            duration_minutes = gcmd.get_int("TIME", None)
        if duration_minutes is None:
            hours = gcmd.get_float("HOURS", None)
            duration_minutes = int(hours * 60) if hours is not None else 240
        if not 1 <= duration_minutes <= 1440:
            raise gcmd.error(
                "[BOX]: Duration %d minutes out of range (1..1440)"
                % duration_minutes)

        mode = gcmd.get_int("MODE", 0)

        # AC power interlock check
        reply = self.box_replies.get(box_addr)
        if reply is not None:
            if not getattr(reply, "dryer_supported", False):
                self._warn(
                    "CFS box %d did not report dryer support. Sending dry command anyway."
                    % box_addr)
            if not getattr(reply, "ac_connected", True):
                self._warn(
                    "CFS box %d: Mains AC power is NOT connected! Chamber will not heat until AC cord is plugged in."
                    % box_addr)

        try:
            res = driver.set_dry_mode(
                channel_mask, target_temp, duration_minutes, mode=mode)
            self._require_reply(res, "CFS box %d set dry mode" % box_addr)
        except Exception as exc:
            raise gcmd.error("[BOX]: BOX_SET_DRY_MODE failed: %s" % exc)

        self.dryer_last_targets[box_addr] = {
            "channel_mask": channel_mask,
            "target_temp": target_temp,
            "duration_minutes": duration_minutes,
            "mode": mode,
        }

        ch_desc = (
            "Left" if channel_mask == 1
            else ("Right" if channel_mask == 2 else "Left & Right"))
        self._info(
            gcmd,
            "CFS box %d: Started drying (%s chamber, %dC, %d minutes)"
            % (box_addr, ch_desc, target_temp, duration_minutes))

    def cmd_get_dry_mode(self, gcmd):
        box_addr = gcmd.get_int("BOX", None)
        if box_addr is None:
            box_addr = gcmd.get_int("ADDR", 1)
        driver = self.drivers.get(box_addr)
        if driver is None:
            raise gcmd.error("[BOX]: CFS box %d is not online" % box_addr)

        reply = self.box_replies.get(box_addr)
        if reply is None:
            reply = driver.query_box_state(timeout=0.5)
        if reply is None:
            raise gcmd.error("[BOX]: CFS box %d did not respond to state query" % box_addr)

        ac_str = "CONNECTED" if getattr(reply, "ac_connected", False) else "DISCONNECTED"
        dryer_sup = "YES" if getattr(reply, "dryer_supported", False) else "NO"
        ch0_heat = "HEATING" if getattr(reply, "ch0_heating", False) else "OFF"
        ch1_heat = "HEATING" if getattr(reply, "ch1_heating", False) else "OFF"

        self._info(gcmd, "=== CFS Box %d Dryer Status ===" % box_addr)
        self._info(gcmd, "Dryer Hardware: %s | AC Power: %s" % (dryer_sup, ac_str))
        self._info(
            gcmd,
            "Left Chamber  (Ch0): %s | Cur: %dC | Target: %dC | Rem: %d min"
            % (ch0_heat, getattr(reply, "ch0_cur_temp", 0),
               getattr(reply, "ch0_target_temp", 0),
               getattr(reply, "ch0_remaining_time", 0)))
        self._info(
            gcmd,
            "Right Chamber (Ch1): %s | Cur: %dC | Target: %dC | Rem: %d min"
            % (ch1_heat, getattr(reply, "ch1_cur_temp", 0),
               getattr(reply, "ch1_target_temp", 0),
               getattr(reply, "ch1_remaining_time", 0)))
        if getattr(reply, "temp_c", None) is not None:
            self._info(
                gcmd,
                "Chamber Ambient: %dC | Humidity: %d%%"
                % (reply.temp_c, reply.humidity_pct or 0))

    def cmd_pause_dry(self, gcmd):
        box_addr = gcmd.get_int("BOX", None)
        if box_addr is None:
            box_addr = gcmd.get_int("ADDR", 1)
        driver = self.drivers.get(box_addr)
        if driver is None:
            raise gcmd.error("[BOX]: CFS box %d is not online" % box_addr)

        params = gcmd.get_command_parameters()
        channel_mask = 3
        if "CH" in params or "CHANNEL" in params:
            ch_val = params.get("CH") if "CH" in params else params.get("CHANNEL")
            ch_raw = str(ch_val).strip().upper()
            if ch_raw in ("0", "LEFT", "L", "CH0"):
                channel_mask = 1
            elif ch_raw in ("1", "RIGHT", "R", "CH1"):
                channel_mask = 2
            elif ch_raw in ("2", "BOTH", "ALL", "CH2"):
                channel_mask = 3
            else:
                try:
                    c_int = int(ch_raw)
                    channel_mask = 1 if c_int == 0 else (2 if c_int == 1 else 3)
                except ValueError:
                    pass

        try:
            res = driver.stop_dry(channel_mask)
            self._require_reply(res, "CFS box %d pause dry" % box_addr)
        except Exception as exc:
            raise gcmd.error("[BOX]: BOX_PAUSE_DRY failed: %s" % exc)

        ch_desc = (
            "Left" if channel_mask == 1
            else ("Right" if channel_mask == 2 else "Left & Right"))
        self._info(gcmd, "CFS box %d: Paused drying (%s chamber)" % (box_addr, ch_desc))

    def cmd_continue_dry(self, gcmd):
        box_addr = gcmd.get_int("BOX", None)
        if box_addr is None:
            box_addr = gcmd.get_int("ADDR", 1)
        driver = self.drivers.get(box_addr)
        if driver is None:
            raise gcmd.error("[BOX]: CFS box %d is not online" % box_addr)

        last = self.dryer_last_targets.get(box_addr)
        reply = self.box_replies.get(box_addr)

        channel_mask = last["channel_mask"] if last else 3
        target_temp = last["target_temp"] if last else 50
        duration_minutes = last["duration_minutes"] if last else 240
        mode = last["mode"] if last else 0

        if reply is not None:
            rem_ch0 = getattr(reply, "ch0_remaining_time", 0)
            rem_ch1 = getattr(reply, "ch1_remaining_time", 0)
            rem_max = max(rem_ch0, rem_ch1)
            if rem_max > 0:
                duration_minutes = rem_max
            tgt_max = max(
                getattr(reply, "ch0_target_temp", 0),
                getattr(reply, "ch1_target_temp", 0))
            if tgt_max >= 30:
                target_temp = tgt_max

        try:
            res = driver.set_dry_mode(
                channel_mask, target_temp, duration_minutes, mode=mode)
            self._require_reply(res, "CFS box %d resume dry" % box_addr)
        except Exception as exc:
            raise gcmd.error("[BOX]: CONTINUE_PAUSE_DRY failed: %s" % exc)

        self._info(
            gcmd,
            "CFS box %d: Resumed drying (%dC, %d minutes remaining)"
            % (box_addr, target_temp, duration_minutes))

    def cmd_set_auto_dry(self, gcmd):
        enabled = bool(gcmd.get_int("ENABLE", 1, minval=0, maxval=1))
        self.dryer_auto_enabled = enabled
        self._info(gcmd, "CFS auto-dry %s" % ("enabled" if enabled else "disabled"))

    def cmd_set_auto_humidity(self, gcmd):
        enabled = bool(gcmd.get_int("ENABLE", 1, minval=0, maxval=1))
        self.auto_humidity_enabled = enabled
        threshold = gcmd.get_int("THRESHOLD", None)
        if threshold is None:
            threshold = gcmd.get_int("HUMIDITY", 25)
        if not 5 <= threshold <= 80:
            raise gcmd.error(
                "[BOX]: HUMIDITY threshold %d%% out of range (5..80%%)" % threshold)
        self.auto_humidity_threshold = threshold
        self._info(
            gcmd,
            "CFS auto-humidity %s (target <= %d%% RH)"
            % ("enabled" if enabled else "disabled", threshold))

    def _on_print_complete_dry(self, *args):
        if not self.dryer_auto_enabled:
            return
        _dlog("print complete: checking auto dry")
        snap = self.snapshot
        if (snap and snap.dryer_supported and snap.ac_connected
                and not (snap.ch0_heating or snap.ch1_heating)):
            box_addr = snap.path_box or (min(self.drivers) if self.drivers else None)
            if box_addr and box_addr in self.drivers:
                try:
                    self.drivers[box_addr].set_dry_mode(3, 50, 240, mode=1)
                    self._info(
                        self.gcode,
                        "CFS auto-dry started after print completion (50C, 240 min)")
                except Exception as exc:
                    _dlog("auto-dry after print failed: %s", exc)

    def _check_auto_humidity(self, snap):
        if not snap.dryer_supported or not snap.ac_connected:
            return
        if snap.ch0_heating or snap.ch1_heating:
            return
        if snap.humidity_pct is not None and snap.humidity_pct > self.auto_humidity_threshold:
            _dlog(
                "CFS auto humidity triggered (humidity %d%% > threshold %d%%)",
                snap.humidity_pct, self.auto_humidity_threshold)
            path_box = snap.path_box or (min(self.drivers) if self.drivers else None)
            if path_box and path_box in self.drivers:
                try:
                    self.drivers[path_box].set_dry_mode(3, 50, 120, mode=1)
                except Exception as exc:
                    _dlog("CFS auto humidity trigger failed: %s", exc)
