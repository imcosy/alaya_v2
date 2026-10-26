// voice.js — 语音采集（全时 VAD + 流式上送）+ PCM 流式播放器
//
// 全双工打断设计（与 ai_me server / mdn 声纹探针配套）：
//   1. VAD 全时开启：TTS 播放期间照常检测（echoMode 下回声基线动态提门限，
//      需连续 512ms 明显超过回声音量才判 speech-start，残余回声不会误触发）
//   2. speech-start 后立即流式上送：pre-roll 缓冲 + 逐帧实时发送，
//      服务端收到前 400ms 即做声纹探针判定（用户插话 or AI回声/杂音）
//   3. 用户说完（音量骤降持续 500ms）→ speech-end → 服务端整句 ASR
//   4. 打断决策在服务端：客户端只负责检测与上送，不回采自判
(function () {
  // ============ 麦克风采集 + 全时 VAD + 流式上送 ============
  // 16kHz 单声道，AudioWorklet 采集 Float32（2048 样本/帧 = 128ms）
  class Mic {
    constructor() {
      this.ctx = null;
      this.stream = null;
      this.node = null;
      this.listening = false;
      this.onVolume = null;        // 实时音量（UI动效）
      this.onStateChange = null;   // 'speech-start' | 'speech-end'
      this.onStreamStart = null;   // speech-start：开始流式上送（无参数）
      this.onStreamChunk = null;   // 每帧 PCM (ArrayBuffer, Int16, 16kHz, 128ms)
      this.onStreamEnd = null;     // speech-end：结束上送（音频已全部通过 chunk 发出）
      this._echoMode = false;      // TTS 播放中（回声计量模式：门限动态提高）
      this._echoBaseline = 0;      // 回声基线（滑动平均，TTS 播放期间持续更新）
      this._cooldownUntil = 0;     // 冷却期时间戳（语句提交后/echoMode 关闭后）
      this._reset();
    }

    _reset() {
      this._preRoll = [];          // 预滚缓冲（说话前 300ms，防切语句开头）
      this._preRollSamples = 0;
      this._speaking = false;
      this._loudFrames = 0;
      this._silentMs = 0;
      this._speechMs = 0;
    }

    // TTS 播放开始/结束时调用
    // 开启后：持续计量麦克风收到的回声音量作为基线，门限 = max(0.05, 基线*1.8)，
    // 且要求连续 4 帧（512ms）超门限才触发 speech-start（用户近场说话远大于回声）
    setEchoMode(on) {
      const was = this._echoMode;
      this._echoMode = !!on;
      this._echoBaseline = 0;
      if (was && !on) {
        // TTS 播完：300ms 冷却，扬声器余音/房间混响不触发
        this._cooldownUntil = Date.now() + 300;
        this._loudFrames = 0;
      }
    }

    async start() {
      if (this.listening) return;
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      this.ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
      if (this.ctx.state === 'suspended') await this.ctx.resume();
      const src = this.ctx.createMediaStreamSource(this.stream);

      // 内联 AudioWorklet：聚合 2048 样本后回传主线程
      const workletCode = `
        class Cap extends AudioWorkletProcessor {
          constructor(){ super(); this.buf=[]; }
          process(inputs){
            const ch = inputs[0] && inputs[0][0];
            if (ch) {
              this.buf.push(...ch);
              if (this.buf.length >= 2048) {
                this.port.postMessage(new Float32Array(this.buf.splice(0, 2048)));
              }
            }
            return true;
          }
        }
        registerProcessor('cap', Cap);
      `;
      const blobUrl = URL.createObjectURL(new Blob([workletCode], { type: 'application/javascript' }));
      await this.ctx.audioWorklet.addModule(blobUrl);
      URL.revokeObjectURL(blobUrl);
      this.node = new AudioWorkletNode(this.ctx, 'cap');
      this.node.port.onmessage = (e) => this._onChunk(e.data);
      src.connect(this.node);
      // 不连接 destination，避免回放
      this._reset();
      this.listening = true;
    }

    _floatToInt16(float32) {
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {
        const s = Math.max(-1, Math.min(1, float32[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      return int16.buffer;
    }

    _onChunk(float32) {
      if (!this.listening) return;
      // RMS 能量
      let sum = 0;
      for (let i = 0; i < float32.length; i++) sum += float32[i] * float32[i];
      const rms = Math.sqrt(sum / float32.length);
      if (this.onVolume) this.onVolume(Math.min(1, rms * 8));

      const frameMs = (float32.length / 16000) * 1000; // 128ms
      // 固定门限（不做噪声底自适应跟踪——远场/噪音环境下噪声底会被污染漂移，
      // 固定门限 + 滞回才是"直接有效"的判定）：
      //   - 非说话态触发门限 0.05（近场说话 RMS 典型 0.08-0.3）
      //   - 说话中维持门限 *0.4（滞回，避免字间弱音把语句切断）
      let thresh = this._speaking ? 0.05 * 0.4 : 0.05;

      // TTS 播放中：回声计量（AI 声音经扬声器回灌麦克风）
      if (this._echoMode) {
        if (rms > 0.005) {
          this._echoBaseline = this._echoBaseline * 0.92 + rms * 0.08;
        }
        // 打断门限 = max(固定门限, 回声基线*1.8)：用户说话要明显比回声响
        if (this._echoBaseline > 0) {
          thresh = Math.max(thresh, this._echoBaseline * 1.8);
        }
      }

      const loud = rms > thresh;

      if (loud) {
        this._loudFrames++;
        this._silentMs = 0;
        if (!this._speaking) {
          // echoMode 下要求连续 4 帧（512ms）：残余回声是断续的，用户说话是持续的
          const needFrames = this._echoMode ? 4 : 2;
          if (this._loudFrames >= needFrames) {
            if (Date.now() < this._cooldownUntil) return;
            this._speaking = true;
            this._speechMs = 0;
            // speech-start：先通知，再把 pre-roll + 当前帧立即上送（语句开头不丢）
            if (this.onStateChange) this.onStateChange('speech-start');
            if (this.onStreamStart) this.onStreamStart();
            for (const c of this._preRoll) {
              if (this.onStreamChunk) this.onStreamChunk(this._floatToInt16(c));
            }
            this._preRoll = [];
            this._preRollSamples = 0;
            if (this.onStreamChunk) this.onStreamChunk(this._floatToInt16(float32));
          } else {
            // 触发前的响帧也进预滚：这些是语音 onset（声纹探针需要足够语音时长，不能丢）
            this._preRoll.push(float32);
            this._preRollSamples += float32.length;
            while (this._preRollSamples > 4800 && this._preRoll.length > 0) {
              this._preRollSamples -= this._preRoll[0].length;
              this._preRoll.shift();
            }
          }
          return;
        }
      } else {
        this._loudFrames = 0;
        if (!this._speaking) {
          // 非语音段进预滚缓冲（保留最近 300ms）
          this._preRoll.push(float32);
          this._preRollSamples += float32.length;
          while (this._preRollSamples > 4800 && this._preRoll.length > 0) {
            this._preRollSamples -= this._preRoll[0].length;
            this._preRoll.shift();
          }
          return;
        }
      }

      // 说话中：逐帧实时上送
      if (this._speaking) {
        if (this.onStreamChunk) this.onStreamChunk(this._floatToInt16(float32));
        if (loud) this._silentMs = 0; else this._silentMs += frameMs;
        this._speechMs += frameMs;
        const tooLong = this._speechMs > 30000;
        // 音量骤降持续 800ms 即判停（给用户思考/换气/字间停顿留余量，避免半句话被截断；
        // 说话时长不足 500ms 的瞬时噪声流，由服务端按 <200ms 不成句规则丢弃）
        const end = this._silentMs >= 800;
        if (end || tooLong) {
          this._speaking = false;
          this._speechMs = 0;
          this._silentMs = 0;
          this._loudFrames = 0;
          this._cooldownUntil = Date.now() + 200; // 提交后冷却，避免余音立即触发新流
          if (this.onStateChange) this.onStateChange('speech-end');
          // 统一发送 streamEnd：过短的流由服务端按 <200ms 不成句规则丢弃
          if (this.onStreamEnd) this.onStreamEnd();
        }
      }
    }

    stop() {
      this.listening = false;
      try { this.node && this.node.disconnect(); } catch (e) {}
      try { this.ctx && this.ctx.close(); } catch (e) {}
      try { this.stream && this.stream.getTracks().forEach(t => t.stop()); } catch (e) {}
      this.ctx = null; this.node = null; this.stream = null;
      this._reset();
    }
  }

  // ============ PCM 流式播放器（48kHz Int16） ============
  class PcmPlayer {
    constructor() {
      this.ctx = null;
      this.nextTime = 0;
      this.sources = [];
      this.playing = false;
      this.onDrain = null; // 全部播完回调
      this._drainTimer = null;
      this._pending = [];      // AudioContext 解锁前到达的音频块
      // 浏览器禁止无手势创建 AudioContext。必须在构造时就绑定手势监听——
      // 若等第一个音频块到达才绑定，而用户手势发生在音频块之前（点麦克风→说话→AI回复），
      // 监听绑定晚于手势，永远错过解锁，所有音频块积压 _pending → 无声。
      // 事件用 touchstart+mousedown 双通道：pointerdown 在老 webview（微信X5等）不存在。
      const fn = () => this.unlock();
      window.addEventListener('touchstart', fn, { once: true });
      window.addEventListener('mousedown', fn, { once: true });
      window.addEventListener('keydown', fn, { once: true });
    }

    // 首次用户手势时解锁：创建 AudioContext 并播出积压的音频块
    unlock() {
      if (this.ctx) return;
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      const q = this._pending;
      this._pending = [];
      for (const b of q) this.push(b);
    }

    _ensure() {
      if (!this.ctx) return false;
      if (this.ctx.state === 'suspended') this.ctx.resume();
      return true;
    }

    // base64 Int16 PCM (48kHz mono)
    push(base64) {
      if (!this._ensure()) {
        this._pending.push(base64);
        if (this._pending.length > 400) this._pending = this._pending.slice(-400); // 防爆内存
        return;
      }
      const bin = atob(base64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const int16 = new Int16Array(bytes.buffer);
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x8000;

      const buf = this.ctx.createBuffer(1, float32.length, 48000); // example.com TTS 输出 48kHz Int16
      buf.getChannelData(0).set(float32);
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this.ctx.destination);
      const now = this.ctx.currentTime;
      const start = Math.max(now + 0.03, this.nextTime);
      src.start(start);
      this.nextTime = start + buf.duration;
      this.playing = true;
      this.sources.push(src);
      src.onended = () => {
        this.sources = this.sources.filter(s => s !== src);
        this._checkDrain();
      };
    }

    _checkDrain() {
      if (this._drainTimer) clearTimeout(this._drainTimer);
      this._drainTimer = setTimeout(() => {
        if (this.playing && this.ctx.currentTime >= this.nextTime - 0.05) {
          this.playing = false;
          if (this.onDrain) this.onDrain();
        } else if (this.playing) {
          this._checkDrain();
        }
      }, 120);
    }

    stop() {
      for (const s of this.sources) { try { s.stop(); } catch (e) {} }
      this.sources = [];
      this.nextTime = 0;
      this.playing = false;
      this._pending = []; // 打断时丢弃排队未播的音频
    }
  }

  window.Voice = { Mic, PcmPlayer };
})();
