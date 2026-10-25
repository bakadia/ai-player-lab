using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using MelonLoader;
using UnityEngine;

// 注意：MelonGameAttribute 的签名是 (developer, name)，顺序不能反。
// 开发者在 MelonLoader 日志里是 "Game Developer: TheJaspel"，不是 "Jaspel"——
// 写成 Jaspel 会被判定为 "is incompatible: ... only compatible with 'Backpack Hero' by Jaspel"
// 然后静默变成 "0 Mods loaded"。详见 docs/PITFALLS.md E1。
[assembly: MelonInfo(typeof(BphBridge.BridgeMod), "BphBridge", "0.2.0", "ai-player-lab")]
[assembly: MelonGame("TheJaspel", "Backpack Hero")]

namespace BphBridge
{
    /// <summary>
    /// 游戏内执行器。三件事：读状态、算合法动作、执行动作。
    /// 现在做了 1 和 3；「算合法动作」还只是把能量费用算出来，没有完整 legal 列表。
    /// </summary>
    public class BridgeMod : MelonMod
    {
        private const float PollInterval = 0.1f;   // 10 Hz，回合制够用

        private string _lastBody;
        private long _seq;
        private float _nextPoll;
        private int _writes;
        private long _errors;
        private float _nextErrorLog;

        private PendingCommand _pending;
        private long _pendingSeqBefore;

        // 探针分段：开局后一次（背包+运行类型），玩家回合一次（敌人意图）
        private int _probeStage;

        public override void OnInitializeMelon()
        {
            RuntimePaths.Init(typeof(BridgeMod).Assembly.Location);
            LoggerInstance.Msg("BphBridge loaded. game=" + Application.productName);
            LoggerInstance.Msg("[BphBridge] runtime = " + RuntimePaths.Dir);
            LoggerInstance.Msg("[BphBridge] poll=" + PollInterval + "s settle=" + Actions.SettleSeconds + "s");
        }

        public override void OnUpdate()
        {
            float now = Time.realtimeSinceStartup;
            if (now < _nextPoll) return;
            _nextPoll = now + PollInterval;

            try
            {
                TryProbe(now);
                TickActions(now);
                TickState(now);
            }
            catch (Exception ex)
            {
                _errors++;
                if (now >= _nextErrorLog)
                {
                    _nextErrorLog = now + 5f;
                    LoggerInstance.Warning("[BphBridge] tick error (total " + _errors + "): " + ex.Message);
                }
            }
        }

        public override void OnApplicationQuit()
        {
            LoggerInstance.Msg("[BphBridge] quit. writes=" + _writes + " seq=" + _seq + " errors=" + _errors);
        }

        // ---------- 状态 ----------

        private void TickState(float now)
        {
            // 内容不变就不重写：seq 只在状态真的变了才 +1。
            // 传 0,0 只是为了拿到「不含 seq/ts」的可比较正文。
            string body = StateReader.Build(0, 0);
            if (body == _lastBody) return;

            _seq++;
            _lastBody = body;

            string doc = StateReader.Build(_seq, NowMs());
            WriteAtomic(RuntimePaths.StateFile, doc);

            _writes++;
            if (_writes <= 5 || _writes % 200 == 0)
            {
                LoggerInstance.Msg("[BphBridge] state #" + _writes + " seq=" + _seq + " bytes=" + doc.Length);
            }
        }

        // ---------- 动作 ----------

        private void TickActions(float now)
        {
            bool halted = false;
            try { halted = File.Exists(RuntimePaths.HaltFile); } catch { }

            if (halted)
            {
                if (_pending != null) FinishPending("halted", now);
                RejectQueued("halted");
                return;
            }

            if (_pending == null)
            {
                PendingCommand pc = null;
                try { pc = Actions.TakeNext(); }
                catch (Exception ex) { LoggerInstance.Warning("[BphBridge] queue error: " + ex.Message); }
                if (pc == null) return;

                _pending = pc;
                _pendingSeqBefore = _seq;
                pc.Before = Snapshot.Take();
                pc.BeforeBody = StateReader.Build(0, 0);
                pc.LastBody = pc.BeforeBody;
                pc.StartTime = now;
                pc.LastChangeTime = now;
                pc.WaitPlayerTurn = MiniJson.Str(pc.Action, "type", "") == "end_turn";

                try
                {
                    Actions.Execute(pc);
                }
                catch (Exception ex)
                {
                    pc.Error = ex.GetType().Name + ": " + ex.Message;
                }
                pc.Executed = true;
                return;
            }

            // 已经在执行：等状态稳定
            string body = StateReader.Build(0, 0);
            if (body != _pending.LastBody)
            {
                _pending.LastBody = body;
                _pending.LastChangeTime = now;
            }

            float elapsedMs = (now - _pending.StartTime) * 1000f;
            bool settled = (now - _pending.LastChangeTime) >= Actions.SettleSeconds;
            bool timedOut = elapsedMs >= _pending.TimeoutMs;
            bool phaseReady = !_pending.WaitPlayerTurn || Actions.PhaseIsPlayerOrIdle();

            if (settled && phaseReady)
            {
                FinishPending(null, now);
                return;
            }

            if (timedOut)
            {
                if (_pending.Error == null)
                {
                    _pending.Error = "timeout after " + (int)elapsedMs + "ms"
                        + (phaseReady ? "" : " (battlePhase never came back to player/idle)");
                }
                FinishPending(null, now);
            }
        }

        private void FinishPending(string error, float now)
        {
            PendingCommand pc = _pending;
            _pending = null;
            if (pc == null) return;
            if (error != null) pc.Error = error;

            Snapshot after = Snapshot.Take();
            bool hasSnapshots = pc.Before != null && after != null;
            string diff = hasSnapshots ? pc.Before.DiffJson(after) : "{}";
            // 注意：用 Differs 判断，不要用 diff != "{}"
            bool changed = hasSnapshots && pc.Before.Differs(after);
            int ms = (int)((now - pc.StartTime) * 1000f);

            Actions.WriteAck(pc, _pendingSeqBefore, _seq, after, changed, ms);

            LoggerInstance.Msg("[BphBridge] ack id=" + pc.Id
                + " ok=" + (pc.Error == null && changed)
                + " changed=" + changed
                + " ms=" + ms
                + " delta=" + diff
                + (pc.Error == null ? "" : " err=" + pc.Error));
        }

        private void RejectQueued(string reason)
        {
            for (int guard = 0; guard < 32; guard++)
            {
                PendingCommand pc = null;
                try { pc = Actions.TakeNext(); }
                catch { }
                if (pc == null) return;
                pc.Error = reason;
                Actions.WriteAck(pc, _seq, _seq, Snapshot.Take(), false, 0);
            }
        }

        // ---------- 探针 ----------

        private void TryProbe(float now)
        {
            if (_probeStage >= 2) return;

            GameManager gm = null;
            Player pl = null;
            GameFlowManager gf = null;
            try { gm = GameManager.main; } catch { }
            try { pl = Player.main; } catch { }
            try { gf = GameFlowManager.main; } catch { }
            if (gm == null || pl == null) return;

            int floor = -1;
            try { floor = gm.floor; } catch { }
            if (floor < 1) return;                       // 还没开局

            if (_probeStage == 0)
            {
                LoggerInstance.Msg("[BphBridge] ===== probe #1 (run started, floor=" + floor + ") =====");
                DumpProbe();
                _probeStage = 1;
                return;
            }

            // 意图要在玩家回合取：enemyTurn 时 myNextAttack 已经清空（SPIKE-02 §3.1）
            if (_probeStage == 1)
            {
                int phase = -1;
                try { if (gf != null) phase = (int)gf.battlePhase; } catch { }
                if (phase != 1) return;

                int alive = 0;
                try
                {
                    List<Enemy> es = Enemy.allEnemies;
                    if (es != null)
                    {
                        for (int i = 0; i < es.Count; i++)
                        {
                            Enemy e = es[i];
                            if (e != null && !e.dead) alive++;
                        }
                    }
                }
                catch { }
                if (alive <= 0) return;

                LoggerInstance.Msg("[BphBridge] ===== probe #2 (playerTurn, alive=" + alive + ") =====");
                DumpProbe();
                _probeStage = 2;
            }
        }

        private void DumpProbe()
        {
            List<string> lines = StateReader.Probe();
            for (int i = 0; i < lines.Count; i++) LoggerInstance.Msg(lines[i]);
        }

        // ---------- 工具 ----------

        private static long NowMs()
        {
            return (long)(DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalMilliseconds;
        }

        /// <summary>先写临时文件再替换，避免 agent 读到半个 JSON。</summary>
        private static void WriteAtomic(string path, string text)
        {
            string tmp = path + ".tmp";
            File.WriteAllText(tmp, text, new UTF8Encoding(false));
            try
            {
                if (File.Exists(path)) File.Replace(tmp, path, null);
                else File.Move(tmp, path);
            }
            catch (Exception)
            {
                // File.Replace 在目标被占用时会失败，退一步直接覆盖
                File.Copy(tmp, path, true);
                try { File.Delete(tmp); } catch { }
            }
        }
    }
}
