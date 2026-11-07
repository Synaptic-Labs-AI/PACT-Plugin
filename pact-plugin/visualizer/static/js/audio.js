/* PACT Mission Control — synthesized 8-bit comms blips.
 * WebAudio only and fully optional: the AudioContext is created lazily the
 * first time sound is enabled, so a muted page allocates nothing and a missing
 * WebAudio implementation is a silent no-op. No audio files are loaded. */

const PATTERNS = {
  dispatch: [
    { f: 660, t: 0, d: 0.06, v: 0.08, w: "square" },
    { f: 990, t: 0.07, d: 0.08, v: 0.07, w: "square" },
  ],
  handoff: [
    { f: 520, to: 780, t: 0, d: 0.12, v: 0.09, w: "sine" },
    { f: 880, t: 0.1, d: 0.07, v: 0.06, w: "sine" },
  ],
  commit: [
    { f: 880, t: 0, d: 0.05, v: 0.08, w: "square" },
    { f: 660, t: 0.06, d: 0.05, v: 0.07, w: "square" },
    { f: 1320, t: 0.12, d: 0.08, v: 0.06, w: "square" },
  ],
  bug: [
    { f: 240, to: 120, t: 0, d: 0.16, v: 0.08, w: "sawtooth" },
  ],
  pr: [
    { f: 523, t: 0, d: 0.07, v: 0.07, w: "square" },
    { f: 659, t: 0.08, d: 0.07, v: 0.07, w: "square" },
    { f: 988, t: 0.16, d: 0.12, v: 0.06, w: "square" },
  ],
  alert: [
    { f: 220, t: 0, d: 0.12, v: 0.09, w: "square" },
    { f: 220, t: 0.16, d: 0.12, v: 0.09, w: "square" },
    { f: 180, t: 0.32, d: 0.16, v: 0.08, w: "square" },
  ],
  achievement: [
    { f: 523, t: 0, d: 0.07, v: 0.08, w: "triangle" },
    { f: 659, t: 0.08, d: 0.07, v: 0.08, w: "triangle" },
    { f: 784, t: 0.16, d: 0.07, v: 0.08, w: "triangle" },
    { f: 1047, t: 0.24, d: 0.2, v: 0.08, w: "triangle" },
  ],
  info: [
    { f: 440, t: 0, d: 0.06, v: 0.05, w: "square" },
  ],
};

export function createAudio() {
  let audioContext = null;
  let masterGain = null;
  let enabled = false;
  let unavailable = false;

  function ensureContext() {
    if (audioContext || unavailable) return audioContext;
    const Ctor = typeof window !== "undefined"
      ? window.AudioContext || window.webkitAudioContext
      : null;
    if (!Ctor) {
      unavailable = true;
      return null;
    }
    try {
      audioContext = new Ctor();
      masterGain = audioContext.createGain();
      masterGain.gain.value = 0.5;
      masterGain.connect(audioContext.destination);
      /* A saved "sound on" preference creates the context at load, before any
       * user gesture, so the browser starts it suspended and every blip is
       * silent. Resume it on the first gesture so the button tells the truth. */
      if (audioContext.state === "suspended" && typeof window.addEventListener === "function") {
        const unlock = () => {
          window.removeEventListener("pointerdown", unlock, true);
          window.removeEventListener("keydown", unlock, true);
          resumeContext();
        };
        window.addEventListener("pointerdown", unlock, true);
        window.addEventListener("keydown", unlock, true);
      }
    } catch (_) {
      audioContext = null;
      masterGain = null;
      unavailable = true;
    }
    return audioContext;
  }

  function resumeContext() {
    const context = ensureContext();
    if (context && context.state === "suspended" && typeof context.resume === "function") {
      try {
        const result = context.resume();
        if (result && typeof result.catch === "function") result.catch(() => {});
      } catch (_) {
        /* silence is always acceptable */
      }
    }
    return context;
  }

  function blip(step) {
    if (!audioContext || !masterGain) return;
    try {
      const start = audioContext.currentTime + (Number(step.t) || 0);
      const end = start + Math.max(0.02, Number(step.d) || 0.06);
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.type = step.w || "square";
      oscillator.frequency.setValueAtTime(Math.max(30, Number(step.f) || 440), start);
      if (step.to) oscillator.frequency.exponentialRampToValueAtTime(Math.max(30, Number(step.to)), end);
      const peak = Math.min(0.2, Math.max(0.002, Number(step.v) || 0.06));
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(peak, start + 0.008);
      gain.gain.exponentialRampToValueAtTime(0.0001, end);
      oscillator.connect(gain);
      gain.connect(masterGain);
      oscillator.start(start);
      oscillator.stop(end + 0.03);
    } catch (_) {
      /* a failed blip must never break the mission */
    }
  }

  function play(kind) {
    if (!enabled) return false;
    if (!resumeContext()) return false;
    const pattern = PATTERNS[kind] || PATTERNS.info;
    try {
      for (const step of pattern) blip(step);
    } catch (_) {
      return false;
    }
    return true;
  }

  function setEnabled(next) {
    enabled = Boolean(next);
    if (enabled) resumeContext();
    return enabled;
  }

  return {
    get enabled() {
      return enabled;
    },
    set enabled(value) {
      setEnabled(value);
    },
    setEnabled,
    play,
  };
}