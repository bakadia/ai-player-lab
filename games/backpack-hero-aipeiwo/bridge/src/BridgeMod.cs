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
[assembly: MelonInfo(typeof(BphBridge.BridgeMod), "BphBridge", "0.1.0", "ai-player-lab")]
[assembly: MelonGame("TheJaspel", "Backpack Hero")]

namespace BphBridge
{
    /// <summary>
    /// 游戏内执行器。只做三件事：读状态、算合法动作、执行动作。
    /// 现在只实现了第一件（写 runtime/state.json），动作层留给下一步。
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

        // 探针分两段：开局后一次（看背包），进战斗后一次（看敌人）
        private int _probeStage;

        public override void OnInitializeMelon()
        {
            RuntimePaths.Init(typeof(BridgeMod).Assembly.Location);
            LoggerInstance.Msg("BphBridge loaded. game=" + Application.productName);
            LoggerInstance.Msg("[BphBridge] runtime = " + RuntimePaths.Dir);
        }

        public override void OnUpdate()
        {
            float now = Time.realtimeSinceStartup;
            if (now < _nextPoll) return;
            _nextPoll = now + PollInterval;

            try
            {
                TryProbe(now);

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
                    LoggerInstance.Msg("[BphBridge] state #" + _writes
                        + " seq=" + _seq + " bytes=" + doc.Length);
                }
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

        // ---------- 探针 ----------

        private void TryProbe(float now)
        {
            if (_probeStage >= 2) return;

            GameManager gm = null;
            Player pl = null;
            try { gm = GameManager.main; } catch { }
            try { pl = Player.main; } catch { }
            if (gm == null || pl == null) return;

            int floor = -1;
            try { floor = gm.floor; } catch { }
            if (floor < 1) return;                       // 还没开局

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

            if (_probeStage == 0)
            {
                LoggerInstance.Msg("[BphBridge] ===== probe #1 (run started, floor=" + floor + ") =====");
                DumpProbe();
                _probeStage = 1;
            }
            else if (_probeStage == 1 && alive > 0)
            {
                LoggerInstance.Msg("[BphBridge] ===== probe #2 (combat, alive=" + alive + ") =====");
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
