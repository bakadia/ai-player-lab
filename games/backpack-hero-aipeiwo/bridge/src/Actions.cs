// 动作层：cmd/*.json -> 执行 -> ack/*.json
//
// 这一层只做三件事，和 MVP §3.1 的约定一致：
//   1. 读命令（runtime/cmd/{id}.json）
//   2. 在主线程执行
//   3. 等状态稳定后回执，带上 delta 与耗时
//
// 关键：**等待不能阻塞主线程**。所以整件事是个跨帧状态机，
// 每一帧只推进一步，不 sleep。

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using MelonLoader;
using UnityEngine;

namespace BphBridge
{
    /// <summary>一次动作前后的关键数值，用来算 delta。</summary>
    internal sealed class Snapshot
    {
        public int PlayerHp = -1;
        public int PlayerMaxHp = -1;
        public int PlayerBlock = -1;
        public int PlayerEnergy = -1;
        public int BattlePhase = -1;
        public int TurnNumber = -1;
        public int Floor = -1;
        public Dictionary<int, int> EnemyHp = new Dictionary<int, int>();
        public Dictionary<int, int> EnemyBlock = new Dictionary<int, int>();

        private static int SI(Func<int> f, int d)
        {
            try { return f(); } catch { return d; }
        }

        public static Snapshot Take()
        {
            Snapshot s = new Snapshot();

            Player pl = null;
            GameManager gm = null;
            GameFlowManager gf = null;
            try { pl = Player.main; } catch { }
            try { gm = GameManager.main; } catch { }
            try { gf = GameFlowManager.main; } catch { }

            Status pst = null;
            try { if (pl != null) pst = pl.stats; } catch { }

            s.PlayerHp = pst == null ? -1 : SI(delegate { return pst.health; }, -1);
            s.PlayerMaxHp = pst == null ? -1 : SI(delegate { return pst.maxHealth; }, -1);
            s.PlayerBlock = pst == null ? -1 : SI(delegate { return pst.armor; }, -1);
            s.PlayerEnergy = pl == null ? -1 : SI(delegate { return pl.AP; }, -1);
            s.BattlePhase = gf == null ? -1 : SI(delegate { return (int)gf.battlePhase; }, -1);
            s.TurnNumber = gf == null ? -1 : SI(delegate { return gf.turnNumber; }, -1);
            s.Floor = gm == null ? -1 : SI(delegate { return gm.floor; }, -1);

            try
            {
                List<Enemy> es = Enemy.allEnemies;
                if (es != null)
                {
                    for (int i = 0; i < es.Count; i++)
                    {
                        Enemy e = es[i];
                        if (e == null) continue;
                        bool dead = SI(delegate { return e.dead ? 1 : 0; }, 1) == 1;
                        if (dead) continue;
                        int id = SI(delegate { return e.GetInstanceID(); }, 0);
                        Status st = null;
                        try { st = e.stats; } catch { }
                        s.EnemyHp[id] = st == null ? -1 : SI(delegate { return st.health; }, -1);
                        s.EnemyBlock[id] = st == null ? -1 : SI(delegate { return st.armor; }, -1);
                    }
                }
            }
            catch { }

            return s;
        }

        /// <summary>算出 after 相对 this 的变化。没有变化就是空对象。</summary>
        public string DiffJson(Snapshot after)
        {
            JsonWriter w = new JsonWriter();
            w.BeginObject();

            if (after.PlayerHp != PlayerHp) w.Prop("player_hp", after.PlayerHp - PlayerHp);
            if (after.PlayerEnergy != PlayerEnergy) w.Prop("player_energy", after.PlayerEnergy - PlayerEnergy);
            if (after.PlayerBlock != PlayerBlock) w.Prop("player_block", after.PlayerBlock - PlayerBlock);

            if (after.BattlePhase != BattlePhase)
            {
                w.Name("battle_phase").BeginArray().Value(BattlePhase).Value(after.BattlePhase).EndArray();
            }
            if (after.TurnNumber != TurnNumber)
            {
                w.Name("turn").BeginArray().Value(TurnNumber).Value(after.TurnNumber).EndArray();
            }
            if (after.Floor != Floor)
            {
                w.Name("floor").BeginArray().Value(Floor).Value(after.Floor).EndArray();
            }

            w.Name("enemies").BeginObject();
            foreach (KeyValuePair<int, int> kv in EnemyHp)
            {
                int now;
                if (!after.EnemyHp.TryGetValue(kv.Key, out now))
                {
                    w.Name("e" + kv.Key).Value("gone");
                    continue;
                }
                if (now != kv.Value) w.Prop("e" + kv.Key, now - kv.Value);
            }
            foreach (KeyValuePair<int, int> kv in after.EnemyHp)
            {
                if (!EnemyHp.ContainsKey(kv.Key)) w.Prop("e" + kv.Key, "appeared");
            }
            w.EndObject();

            w.EndObject();
            return w.ToString();
        }
    }

    /// <summary>一条正在执行的命令。</summary>
    internal sealed class PendingCommand
    {
        public long Id;
        public string CmdPath;
        public Dictionary<string, object> Action;
        public long TimeoutMs = 5000;

        public Snapshot Before;
        public string BeforeBody;
        public string LastBody;

        public float StartTime;
        public float LastChangeTime;
        public bool Executed;
        public string Error;
        public string AckPath;
    }

    internal static class Actions
    {
        public const float SettleSeconds = 0.3f;

        // ---------- 队列 ----------

        /// <summary>取一条待执行命令；没有就返回 null。按修改时间排队。</summary>
        public static PendingCommand TakeNext()
        {
            try
            {
                if (!Directory.Exists(RuntimePaths.CmdDir)) return null;
                string[] files = Directory.GetFiles(RuntimePaths.CmdDir, "*.json");
                if (files.Length == 0) return null;

                Array.Sort(files, delegate (string a, string b)
                {
                    int c = File.GetLastWriteTimeUtc(a).CompareTo(File.GetLastWriteTimeUtc(b));
                    return c != 0 ? c : string.CompareOrdinal(a, b);
                });

                string path = files[0];
                string text = File.ReadAllText(path);
                object parsed = MiniJson.Parse(text);
                Dictionary<string, object> root = MiniJson.Obj(parsed);
                if (root == null) throw new FormatException("command root is not an object");

                PendingCommand pc = new PendingCommand();
                pc.CmdPath = path;
                pc.Id = MiniJson.Num(root, "id", 0);
                pc.TimeoutMs = MiniJson.Num(root, "timeout_ms", 5000);
                if (pc.TimeoutMs <= 0) pc.TimeoutMs = 5000;
                pc.Action = MiniJson.Sub(root, "action");
                if (pc.Action == null) throw new FormatException("missing 'action'");
                pc.AckPath = Path.Combine(RuntimePaths.AckDir, pc.Id + ".json");
                return pc;
            }
            catch (Exception ex)
            {
                MelonLogger.Warning("[BphBridge] bad command file: " + ex.Message);
                // 坏命令直接丢掉，别卡住队列
                try
                {
                    string[] bad = Directory.GetFiles(RuntimePaths.CmdDir, "*.json");
                    if (bad.Length > 0) File.Delete(bad[0]);
                }
                catch { }
                return null;
            }
        }

        // ---------- 执行 ----------

        public static void Execute(PendingCommand pc)
        {
            string type = MiniJson.Str(pc.Action, "type", "");
            if (type == "noop") return;

            if (type == "end_turn")
            {
                GameFlowManager gf = GameFlowManager.main;
                if (gf == null) throw new InvalidOperationException("GameFlowManager.main is null");
                gf.EndTurn();
                return;
            }

            if (type == "use_item")
            {
                string uid = MiniJson.Str(pc.Action, "uid", "");
                Item2 item = FindItem(uid);
                if (item == null) throw new InvalidOperationException("item not found: " + uid);

                GameFlowManager gf = GameFlowManager.main;
                if (gf == null) throw new InvalidOperationException("GameFlowManager.main is null");

                string via = MiniJson.Str(pc.Action, "via", "consider");
                if (via == "useitem")
                {
                    bool force = MiniJson.Bool(pc.Action, "force", false);
                    IEnumerator co = gf.UseItem(item, force, false, item.playerAnimation, true, false);
                    MelonCoroutines.Start(co);
                }
                else
                {
                    gf.ConsiderUse(item, false);
                }
                return;
            }

            throw new InvalidOperationException("unknown action type: " + type);
        }

        private static Item2 FindItem(string uid)
        {
            if (string.IsNullOrEmpty(uid) || uid.Length < 2) return null;
            if (uid[0] != 'i') return null;

            int want;
            if (!int.TryParse(uid.Substring(1), out want)) return null;

            try
            {
                List<Item2> all = Item2.allItems;
                if (all == null) return null;
                for (int i = 0; i < all.Count; i++)
                {
                    Item2 it = all[i];
                    if (it == null) continue;
                    if (it.GetInstanceID() == want) return it;
                }
            }
            catch { }
            return null;
        }

        // ---------- 回执 ----------

        public static void WriteAck(PendingCommand pc, long seqBefore, long seqAfter,
                                    Snapshot after, bool changed, int ms)
        {
            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("id", pc.Id);
            w.Prop("ok", pc.Error == null && changed);
            w.Prop("changed", changed);
            w.Prop("seq_before", seqBefore);
            w.Prop("seq_after", seqAfter);
            w.Prop("ms", ms);

            w.Name("action");
            WriteValue(w, pc.Action);

            w.Name("delta");
            string delta = pc.Before != null && after != null ? pc.Before.DiffJson(after) : "{}";
            w.Raw(delta);

            if (pc.Error == null) w.Name("error").Null();
            else w.Prop("error", pc.Error);

            w.EndObject();

            try
            {
                Directory.CreateDirectory(RuntimePaths.AckDir);
                string tmp = pc.AckPath + ".tmp";
                File.WriteAllText(tmp, w.ToString(), new System.Text.UTF8Encoding(false));
                if (File.Exists(pc.AckPath)) File.Delete(pc.AckPath);
                File.Move(tmp, pc.AckPath);
            }
            catch (Exception ex)
            {
                MelonLogger.Error("[BphBridge] cannot write ack: " + ex.Message);
            }

            try { if (File.Exists(pc.CmdPath)) File.Delete(pc.CmdPath); }
            catch (Exception ex) { MelonLogger.Warning("[BphBridge] cannot delete cmd: " + ex.Message); }
        }

        /// <summary>把解析出来的树原样写回去（ack 里回显 action）。</summary>
        private static void WriteValue(JsonWriter w, object v)
        {
            if (v == null) { w.Null(); return; }

            Dictionary<string, object> d = v as Dictionary<string, object>;
            if (d != null)
            {
                w.BeginObject();
                foreach (KeyValuePair<string, object> kv in d)
                {
                    w.Name(kv.Key);
                    WriteValue(w, kv.Value);
                }
                w.EndObject();
                return;
            }

            List<object> a = v as List<object>;
            if (a != null)
            {
                w.BeginArray();
                for (int i = 0; i < a.Count; i++) WriteValue(w, a[i]);
                w.EndArray();
                return;
            }

            if (v is string) { w.Value((string)v); return; }
            if (v is bool) { w.Value((bool)v); return; }
            if (v is double) { w.Value((float)(double)v); return; }

            w.Value(v.ToString());
        }
    }
}
