let bridge = null;

document.addEventListener("DOMContentLoaded", () => {
  const navButtons = document.querySelectorAll(".nav-item");
  const tabPanels = document.querySelectorAll(".tab-panel");
  const screenTitle = document.getElementById("screen-title");
  const systemTimeEl = document.getElementById("system-time");
  const settingsNavItems = document.querySelectorAll(".settings-nav-item");
  const settingsTabPanels = document.querySelectorAll(".settings-tab-panel");

  const tabTitles = {
    dashboard: "Dashboard (Home)",
    posture: "Posture Monitoring",
    environment: "Environment Monitoring",
    alerts: "Alerts & Notifications",
    history: "History Log",
    analytics: "Analytics & Reports",
    settings: "Settings",
  };

  const $ = (id) => document.getElementById(id);

  function updateClock() {
    const now = new Date();
    let hours = now.getHours();
    const minutes = String(now.getMinutes()).padStart(2, "0");
    const seconds = String(now.getSeconds()).padStart(2, "0");
    const ampm = hours >= 12 ? "PM" : "AM";
    hours = hours % 12 || 12;
    const formattedHours = String(hours).padStart(2, "0");
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    systemTimeEl.textContent = `${formattedHours}:${minutes}:${seconds} ${ampm} | ${months[now.getMonth()]} ${now.getDate()}, ${now.getFullYear()}`;
  }

  navButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tabId = btn.getAttribute("data-tab");
      navButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      tabPanels.forEach((panel) => panel.classList.remove("active"));
      document.getElementById(`panel-${tabId}`).classList.add("active");
      screenTitle.textContent = tabTitles[tabId] || "AI Smart Desk Hub";
    });
  });

  settingsNavItems.forEach((item) => {
    item.addEventListener("click", () => {
      const settingsTabId = item.getAttribute("data-settings-tab");
      settingsNavItems.forEach((b) => b.classList.remove("active"));
      item.classList.add("active");
      settingsTabPanels.forEach((panel) => panel.classList.remove("active"));
      document.getElementById(`settings-${settingsTabId}`).classList.add("active");
    });
  });

  function renderBars(targetId, values, className = "") {
    const target = $(targetId);
    const items = values && values.length ? values : [0];
    const max = Math.max(...items, 1);
    target.innerHTML = items.map((value) => {
      const height = Math.max(10, Math.round((value / max) * 100));
      return `<div class="mini-bar ${className}" style="height:${height}%"></div>`;
    }).join("");
  }

  function average(values) {
    if (!values.length) {
      return 0;
    }
    return values.reduce((sum, value) => sum + value, 0) / values.length;
  }

  function dominantLight(history) {
    if (!history.length) {
      return "Unknown";
    }
    const counts = history.reduce((acc, item) => {
      const key = item.room_light_status || "Unknown";
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
    return Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0];
  }

  function percentage(part, total) {
    if (!total) {
      return 0;
    }
    return (part / total) * 100;
  }

  function setStatusPill(id, on, onText, offText) {
    const pill = $(id);
    pill.textContent = on ? onText : offText;
    pill.parentElement.classList.remove("green", "red");
    pill.parentElement.classList.add(on ? "green" : "red");
  }

  function setCalibrationCheck(id, state) {
    const item = $(id);
    if (!item) {
      return;
    }
    item.classList.remove("ready", "optional");
    if (state === "ready") {
      item.classList.add("ready");
    } else if (state === "optional") {
      item.classList.add("optional");
    }
  }

  function syncLampWidgets(brightness) {
    $("ctrl-lamp-dimmer").value = brightness;
    $("ctrl-lamp-val").textContent = `${brightness}%`;
    $("env-lamp-slider").value = brightness;
    $("env-lamp-slider-val").textContent = `${brightness}%`;
    $("env-detailed-lamp").textContent = `${brightness}%`;
  }

  function syncFanWidgets(isOn) {
    $("ctrl-fan-toggle").checked = isOn;
    $("ctrl-fan-val").textContent = isOn ? "ON" : "OFF";
    $("env-fan-slider-val").textContent = isOn ? "ON" : "OFF";
    $("env-detailed-fan").textContent = isOn ? "ON" : "OFF";
    $("env-fan-status-chip").textContent = isOn ? "On" : "Off";
    $("env-fan-status-txt").textContent = isOn ? "Status: ON" : "Status: OFF";
  }

  function syncLampToggle(isOn) {
    $("ctrl-lamp-toggle").checked = isOn;
    $("env-lamp-status-txt").textContent = isOn ? "Status: ON" : "Status: OFF";
    setStatusPill("pill-lamp", isOn, `ON (${$("ctrl-lamp-dimmer").value}%)`, "OFF");
  }

  function updateStatus(status) {
    const postureBad = status.posture_status === "Bad";
    const fatigueBad = status.fatigue_status === "Drowsy";
    const silentOn = status.silent_mode;
    const lampOn = Number(status.lamp_brightness) > 0;

    $("connectionBadge").textContent = status.esp32_connected ? "CONNECTED" : "SIMULATION";
    $("esp-status-text").textContent = status.esp32_connected ? "Connected" : "Simulation";
    $("esp-status-text").className = `detail-val ${status.esp32_connected ? "text-green" : "text-dim"}`;
    $("esp-status-dot").classList.toggle("connected", status.esp32_connected);
    $("esp-status-dot").classList.toggle("disconnected", !status.esp32_connected);
    $("sideTransport").textContent = status.transport_mode || "HTTP";
    $("sideMode").textContent = status.auto_mode ? "Auto" : "Manual";
    $("alerts-badge").textContent = String(status.today_alerts || 0);
    $("alerts-badge").style.display = (status.today_alerts || 0) > 0 ? "inline-block" : "none";

    $("dashboardPosture").textContent = status.posture_status;
    $("dashboardPostureDetail").textContent = status.posture_detail;
    $("dashboard-posture-badge").textContent = postureBad ? "POSTURE ALERT" : "GOOD POSTURE";
    $("dashboard-posture-badge").className = `status-badge ${postureBad ? "poor" : "good"}`;
    $("dashboard-posture-msg").textContent = postureBad ? status.posture_detail : "Keep it up!";
    $("dashboard-posture-head").setAttribute("stroke", postureBad ? "var(--text-red)" : "var(--text-green)");
    $("dashboard-posture-spine").setAttribute("stroke", postureBad ? "var(--text-red)" : "var(--text-green)");

    $("db-val-light").textContent = `${status.room_light_level}%`;
    $("db-light-state").textContent = status.room_light;
    $("db-val-temp").textContent = `${status.temperature_c.toFixed(1)} C`;
    $("db-val-session").textContent = status.session_time;
    $("db-val-mode").textContent = status.auto_mode ? "Auto" : "Manual";
    $("db-device-state").textContent = status.esp32_connected ? "Connected" : "Simulation";

    $("postureStatus").textContent = status.posture_status;
    $("postureStatus").className = `m-val ${postureBad ? "text-red" : "text-green"}`;
    $("postureDetail").textContent = status.posture_detail;
    $("postureDetail").className = `m-status ${postureBad ? "text-red" : "text-green"}`;
    $("fatigueStatus").textContent = status.fatigue_status;
    $("fatigueStatus").className = `m-val ${fatigueBad ? "text-red" : "text-green"}`;
    $("fatigueDetail").textContent = status.fatigue_detail;
    $("fatigueDetail").className = `m-status ${fatigueBad ? "text-red" : "text-green"}`;
    $("blinkRate").textContent = `${status.blink_rate.toFixed(1)} /min`;
    $("camLight").textContent = `${status.room_light} (${status.room_light_level}%)`;
    $("camTemp").textContent = `${status.temperature_c.toFixed(1)} C`;
    $("camFps").textContent = `${status.fps.toFixed(1)} fps`;
    $("cameraStatus").textContent = status.last_command_status;
    $("cameraHeadline").textContent = fatigueBad ? "Fatigue risk detected in live stream." : "Live wellness monitoring stream.";
    $("camPosture").textContent = `${status.posture_status} posture: ${status.posture_detail}`;
    $("camFatigue").textContent = `${status.fatigue_status}: ${status.fatigue_detail}`;
    $("camDevice").textContent = status.last_command_status;
    $("camSnapshot").textContent = status.last_snapshot_path ? `Snapshot: ${status.last_snapshot_path}` : "No snapshot captured.";
    $("snapshotNote").textContent = status.last_snapshot_path ? `Last snapshot: ${status.last_snapshot_path}` : "No snapshot captured yet.";
    $("camera-ai-overlay-text").textContent = postureBad ? "POSTURE ALERT" : "GOOD POSTURE";
    $("camera-ai-overlay-badge").className = `camera-status-badge ${postureBad ? "red" : "green"}`;
    $("toggleCameraBtn").textContent = status.camera_paused ? "Resume Camera" : "Pause Camera";

    $("lightStatus").textContent = status.room_light;
    $("tempStatus").textContent = `${status.temperature_c.toFixed(1)} C`;
    $("fanDetail").textContent = status.fan_detail;
    $("lampDetail").textContent = status.lamp_detail;
    $("dashboardCommand").textContent = status.last_command_status;
    $("env-light-status").textContent = status.room_light;
    $("env-detailed-light").textContent = `${status.room_light_level}%`;
    $("env-detailed-temp").textContent = `${status.temperature_c.toFixed(1)} C`;

    $("alert-current-status").textContent = status.last_command_status;
    $("alert-silent-status").textContent = silentOn ? "Silent mode enabled." : "Silent mode disabled.";
    $("toggleSilentBtn").textContent = silentOn ? "Disable Silent Mode" : "Enable Silent Mode";
    $("alert-current-time").textContent = status.break_due ? "Break Due" : "Live";

    $("pill-esp32").textContent = status.esp32_connected ? "Connected" : "Simulation";
    $("pill-camera").textContent = status.camera_status || "Live";
    $("pill-ai").textContent = fatigueBad ? "Drowsy" : "Running";
    $("pill-command").textContent = status.last_command_status;
    $("pill-buzzer").textContent = silentOn ? "Silent" : "Ready";
    if (document.getElementById("pill-voice")) {
      setStatusPill("pill-voice", status.voice_listening, "Listening", "Stopped");
    }
    setStatusPill("pill-fan", status.fan_on, "ON", "OFF");

    if ($("pill-calib")) {
      const postureCalib = status.posture_calibrated;
      const earCalib = status.ear_calibrated;
      $("pill-calib").textContent = postureCalib ? (earCalib ? "Ready" : "EAR warming...") : "Calibrating...";
      const calibPill = $("status-pill-calib");
      calibPill.classList.remove("green", "yellow", "red");
      calibPill.classList.add(postureCalib && earCalib ? "green" : postureCalib ? "yellow" : "red");
    }
    if ($("calibrationWorkflowCard")) {
      const progress = Math.max(0, Math.min(100, Number(status.calibration_progress || 0)));
      const postureReady = Boolean(status.posture_calibrated);
      const earReady = Boolean(status.ear_calibrated);
      $("calibrationWorkflowState").textContent = postureReady ? (earReady ? "Ready" : "Eye baseline warming") : "In progress";
      $("calibrationProgressText").textContent = `${progress.toFixed(0)}%`;
      $("calibrationProgressFill").style.width = `${progress}%`;
      $("calibrationStatusText").textContent = status.calibration_status || "Sit upright and keep still.";
      setCalibrationCheck("checkCameraVisible", status.posture_landmarks_visible ? "ready" : "missing");
      setCalibrationCheck("checkHipsVisible", status.hips_visible ? "ready" : "optional");
      setCalibrationCheck("checkEarReady", earReady ? "ready" : "missing");
    }
    if ($("pill-posture-pct")) {
      $("pill-posture-pct").textContent = `${status.posture_good_pct ?? "--"}%`;
      const pct = status.posture_good_pct ?? 0;
      $("status-pill-posture-pct").classList.remove("green", "yellow", "red");
      $("status-pill-posture-pct").classList.add(pct >= 70 ? "green" : pct >= 40 ? "yellow" : "red");
    }
    syncLampWidgets(status.lamp_brightness);
    syncFanWidgets(status.fan_on);
    syncLampToggle(lampOn);

    $("ctrl-buzzer-toggle").checked = !silentOn;
    $("toggleFanBtn").textContent = status.fan_on ? "Turn Fan OFF" : "Turn Fan ON";
    $("autoMode").checked = status.auto_mode;
    $("autoFan").checked = $("autoFan").checked;
    $("silentMode").checked = status.silent_mode;

    $("lightPercent").textContent = `${status.room_light_level}%`;
    $("lightFill").style.width = `${status.room_light_level}%`;

    renderBars("blinkChart", status.blink_history || []);
    renderBars("lightChart", status.light_history || [], "light");
    renderBars("alertChart", status.alert_history || [], "alert");
  }

  function updatePreview(frameBase64) {
    $("cameraPreview").src = `data:image/jpeg;base64,${frameBase64}`;
  }

  function loadSettings(settings) {
    $("cameraIndex").value = settings.camera_index;
    $("postureSensitivity").value = settings.posture_sensitivity;
    $("fatigueSensitivity").value = settings.fatigue_sensitivity;
    $("breakMinutes").value = settings.break_reminder_minutes;
    $("temperatureValue").value = settings.default_temperature_c;
    $("defaultLampBrightness").value = settings.default_lamp_brightness;
    $("autoMode").checked = settings.auto_mode;
    $("silentMode").checked = settings.silent_mode;
    $("fatigueAlert").checked = settings.fatigue_alert;
    $("autoFan").checked = settings.auto_fan;
    $("autoLamp").checked = settings.auto_lamp;
    $("startupAutoLaunch").checked = settings.startup_auto_launch;
    $("minimizeToTray").checked = settings.minimize_to_tray;
    $("deviceEndpoint").value = settings.esp32_url || "";
  }

  function updateReports(payload) {
    const daily = payload.daily || {};
    const history = payload.history || [];
    const recent = history.slice(-8);
    const avgDuration = average(history.map((item) => item.duration_minutes || 0));
    const avgBlinks = average(history.map((item) => item.blink_count || 0));
    const avgPostureAlerts = average(history.map((item) => item.posture_alerts || 0));
    const totalDuration = history.reduce((sum, item) => sum + (item.duration_minutes || 0), 0);
    const totalAlerts = history.reduce((sum, item) => sum + (item.posture_alerts || 0) + (item.fatigue_alerts || 0), 0);
    const goodPostureSessions = history.filter((item) => (item.posture_alerts || 0) <= 1).length;
    const highRiskSessions = history.filter((item) => ((item.posture_alerts || 0) + (item.fatigue_alerts || 0)) >= 4).length;
    const bestSession = history.length
      ? history.reduce((best, item) => ((item.posture_alerts || 0) + (item.fatigue_alerts || 0)) < ((best.posture_alerts || 0) + (best.fatigue_alerts || 0)) ? item : best)
      : null;
    const worstSession = history.length
      ? history.reduce((worst, item) => ((item.posture_alerts || 0) + (item.fatigue_alerts || 0)) > ((worst.posture_alerts || 0) + (worst.fatigue_alerts || 0)) ? item : worst)
      : null;

    $("dailyDate").textContent = daily.date || "--";
    $("dailySessions").textContent = daily.session_count ?? 0;
    $("dailyFocusTime").textContent = `${daily.duration_minutes ?? 0} min`;
    $("dailyPostureAlerts").textContent = daily.posture_alerts ?? 0;
    $("dailyFatigueAlerts").textContent = daily.fatigue_alerts ?? 0;
    $("dailyBlinks").textContent = daily.blink_count ?? 0;
    $("reportSummary").textContent = `${daily.session_count ?? 0} sessions | ${daily.duration_minutes ?? 0} min focus time`;

    const latest = history.length ? history[history.length - 1] : null;
    $("latestSession").textContent = latest
      ? `${latest.duration_minutes || 0} min`
      : "Waiting";

    $("insight-1").textContent = history.length
      ? `Average session length is ${avgDuration.toFixed(1)} minutes across ${history.length} sessions.`
      : "No session history yet for analytics.";
    $("insight-2").textContent = bestSession
      ? `Best session: ${(bestSession.posture_alerts || 0) + (bestSession.fatigue_alerts || 0)} alerts — quality score ${bestSession.quality_score ?? "--"}/100.`
      : "Best session insight will appear after saving reports.";
    $("insight-3").textContent = avgQuality !== null
      ? `Average quality score is ${avgQuality}/100 — ${avgQuality >= 70 ? "Great consistency!" : avgQuality >= 40 ? "Room for improvement." : "Focus on posture."}`
      : "Quality score trends will appear after more sessions.";
    $("insight-4").textContent = history.length
      ? `Typical light condition is ${dominantLight(history)} and average blinks per session is ${avgBlinks.toFixed(1)}.`
      : "Light and blink pattern insights are waiting for data.";

    renderBars("analyticsBlinkChart", recent.map((item) => item.blink_count || 0));
    renderBars("analyticsLightChart", recent.map((item) => item.room_light_status === "Low" ? 25 : 75), "light");
    renderBars(
      "analyticsAlertChart",
      recent.map((item) => (item.posture_alerts || 0) + (item.fatigue_alerts || 0)),
      "alert",
    );

    $("dailyPostureAlerts").textContent = daily.posture_alerts ?? 0;
    $("dailyFatigueAlerts").textContent = daily.fatigue_alerts ?? 0;
    $("dailyBlinks").textContent = daily.blink_count ?? 0;
    $("todayAlerts").textContent = (daily.posture_alerts ?? 0) + (daily.fatigue_alerts ?? 0);
    $("activity-good-posture").textContent = goodPostureSessions;
    $("activity-good-posture-pct").textContent = `(${percentage(goodPostureSessions, history.length).toFixed(0)}%)`;
    $("activity-risk-sessions").textContent = highRiskSessions;
    $("activity-risk-sessions-pct").textContent = `(${percentage(highRiskSessions, history.length).toFixed(0)}%)`;
    $("activity-total-working-time").textContent = `${totalDuration} min`;
    $("activity-alerts-count").textContent = totalAlerts;
    if (history.length) {
      $("dailyPostureAlerts").setAttribute("title", `Average ${avgPostureAlerts.toFixed(1)} posture alerts per session`);
    }

    const avgQuality = history.length
      ? Math.round(history.reduce((sum, item) => sum + (item.quality_score || 0), 0) / history.length)
      : null;
    if ($("avgQualityScore")) {
      $("avgQualityScore").textContent = avgQuality !== null ? `${avgQuality}/100` : "--";
      $("avgQualityScore").className = `s-val ${avgQuality >= 70 ? "text-green" : avgQuality >= 40 ? "text-yellow" : "text-red"}`;
    }

    const rows = history.slice().reverse().map((report) => {
      const score = report.quality_score;
      const scoreColor = score >= 70 ? "color:var(--text-green)" : score >= 40 ? "color:var(--text-yellow)" : "color:var(--text-red)";
      return `
        <tr>
          <td>${new Date((report.timestamp || 0) * 1000).toLocaleString()}</td>
          <td>${report.duration_minutes || 0} min</td>
          <td>${report.posture_alerts || 0}</td>
          <td>${report.fatigue_alerts || 0}</td>
          <td>${report.blink_count || 0}</td>
          <td>${report.room_light_status || "Unknown"}</td>
          <td style="${scoreColor}">${score !== undefined ? `${score}/100` : "--"}</td>
        </tr>
      `;
    }).join("");
    $("reportTableBody").innerHTML = rows || `<tr><td colspan="7">No reports yet.</td></tr>`;
  }

  function bindActions() {
    $("toggleModeBtn").onclick = () => bridge.toggleMode();
    $("toggleSilentBtn").onclick = () => bridge.toggleSilentMode();
    $("toggleFanBtn").onclick = () => bridge.toggleFan();
    $("postureAlertBtn").onclick = () => bridge.triggerPostureAlert();
    $("drowsyAlertBtn").onclick = () => bridge.triggerDrowsyAlert();
    $("toggleCameraBtn").onclick = () => bridge.toggleCamera();
    $("snapshotBtn").onclick = () => bridge.captureSnapshot();

    $("ctrl-lamp-toggle").onchange = (event) => {
      const brightness = event.target.checked ? Number($("ctrl-lamp-dimmer").value) : 0;
      syncLampToggle(event.target.checked);
      bridge.setLampBrightness(brightness);
    };
    $("ctrl-lamp-dimmer").oninput = (event) => {
      syncLampWidgets(Number(event.target.value));
      syncLampToggle(Number(event.target.value) > 0);
      bridge.setLampBrightness(Number(event.target.value));
    };
    $("env-lamp-slider").oninput = (event) => {
      syncLampWidgets(Number(event.target.value));
      syncLampToggle(Number(event.target.value) > 0);
      bridge.setLampBrightness(Number(event.target.value));
    };

    $("ctrl-fan-toggle").onchange = () => bridge.toggleFan();
    $("ctrl-buzzer-toggle").onchange = () => bridge.toggleSilentMode();

    $("saveSettingsBtn").onclick = () => {
      bridge.saveSettings(JSON.stringify({
        esp32_url: $("deviceEndpoint").value || $("deviceEndpoint").placeholder,
        camera_index: Number($("cameraIndex").value),
        posture_sensitivity: Number($("postureSensitivity").value),
        fatigue_sensitivity: Number($("fatigueSensitivity").value),
        fatigue_alert: $("fatigueAlert").checked,
        silent_mode: $("silentMode").checked,
        auto_fan: $("autoFan").checked,
        auto_lamp: $("autoLamp").checked,
        auto_mode: $("autoMode").checked,
        break_reminder_minutes: Number($("breakMinutes").value),
        startup_auto_launch: $("startupAutoLaunch").checked,
        minimize_to_tray: $("minimizeToTray").checked,
        default_temperature_c: Number($("temperatureValue").value),
        default_lamp_brightness: Number($("defaultLampBrightness").value),
      }));
    };

    $("pairDeviceBtn").onclick = () => {
      bridge.pairDevice(JSON.stringify({
        mode: $("deviceMode").value,
        endpoint: $("deviceEndpoint").value,
      }));
    };
    $("discoverDeviceBtn").onclick = () => bridge.discoverDevice();
    $("provisionWifiBtn").onclick = () => {
      bridge.provisionDeviceWifi(JSON.stringify({
        ssid: $("newWifiSsid").value,
        password: $("newWifiPassword").value,
      }));
      $("newWifiPassword").value = "";
    };
    $("syncDeviceBtn").onclick = () => bridge.syncDeviceSettings();
    $("refreshDeviceBtn").onclick = () => bridge.refreshDeviceStatus();
    $("resetCalibrationBtn").onclick = () => bridge.resetCalibration();
    if ($("resetCalibrationInlineBtn")) {
      $("resetCalibrationInlineBtn").onclick = () => bridge.resetCalibration();
    }
    $("btn-clear-all-alerts").onclick = () => {
      $("alert-current-status").textContent = "Status cleared on UI.";
      $("alert-current-time").textContent = "Now";
    };
  }

  window.app = { updateStatus, updatePreview, updateReports };

  updateClock();
  setInterval(updateClock, 1000);

  new QWebChannel(qt.webChannelTransport, (channel) => {
    bridge = channel.objects.bridge;
    const initial = JSON.parse(bridge.initialState());
    updateStatus(initial.status);
    loadSettings(initial.settings);
    updateReports(initial.reports);
    bindActions();
  });
});
