// 结构化战斗回执（SPIKE-07 §4 第二步）。
//
// 只回答三个问题：
//   1. 谁打谁      —— from / to / uid / name
//   2. 挡了多少    —— amount（真掉的血）+ blocked（被护甲吃掉的）
//   3. 上了什么状态 —— kind=status / item_status，带 delta 和最终 value
//
// 数据来源是动作前后的两个 Snapshot。这样不用 Harmony patch，也不会改变游戏行为；
// 代价是「同一帧里多段伤害」会合并成一条，但账本需要的每件物品聚合值已经够了。
//
// C# 5：没有字符串插值 / ?. / nameof / 元组。

using System;
using System.Collections.Generic;

namespace BphBridge
{
    internal static class Receipt
    {
        public static void Write(JsonWriter w, PendingCommand pc, Snapshot after)
        {
            Snapshot before = pc.Before;
            string type = MiniJson.Str(pc.Action, "type", "");
            string itemUid = MiniJson.Str(pc.Action, "uid", "");
            int itemId = ParseUid(itemUid);
            string itemName = "";
            if (before != null && itemId != 0)
            {
                string nm;
                if (before.ItemNames.TryGetValue(itemId, out nm)) itemName = nm == null ? "" : nm;
            }

            w.BeginObject();
            w.Prop("action", type);
            if (itemName.Length > 0) w.Prop("item", itemName);
            w.Prop("source", type == "end_turn" ? "enemies" : "player");

            // retry_run / start_run 会整局重置：前后快照不是同一个世界，
            // 不重置的话会把「旧敌人消失」误报成被玩家打死 40 血。
            bool reset = type == "retry_run" || type == "start_run";
            if (reset) w.Prop("reset", true);

            w.Name("effects").BeginArray();
            if (!reset && before != null && after != null)
            {
                WritePlayer(w, before, after, type, itemName);
                WriteEnemies(w, before, after, type, itemName);
                WriteItems(w, before, after);
            }
            w.EndArray();
            w.EndObject();
        }

        // ---------------------------------------------------------------- player

        private static void WritePlayer(JsonWriter w, Snapshot before, Snapshot after,
                                        string type, string itemName)
        {
            string from = type == "end_turn" ? "enemies" : (itemName.Length > 0 ? itemName : "self");
            int incoming = SumAttack(before);
            int dhp = after.PlayerHp - before.PlayerHp;

            if (dhp < 0)
            {
                int damage = -dhp;
                int blocked = 0;
                if (type == "end_turn")
                {
                    int armor = Math.Max(0, before.PlayerBlock);
                    blocked = Math.Max(0, Math.Min(armor, incoming - damage));
                }
                w.BeginObject();
                w.Prop("kind", "damage");
                w.Prop("from", from);
                w.Prop("to", "player");
                w.Prop("amount", damage);
                w.Prop("blocked", blocked);
                w.Prop("incoming", incoming);
                w.EndObject();
            }
            else if (dhp > 0)
            {
                w.BeginObject();
                w.Prop("kind", "heal");
                w.Prop("from", from);
                w.Prop("to", "player");
                w.Prop("amount", dhp);
                w.EndObject();
            }

            int dblock = after.PlayerBlock - before.PlayerBlock;
            // end_turn 的护甲变化是「被打掉 + 回合结束清空」，已经折进 blocked，
            // 再报一条会重复计数。
            if (dblock != 0 && type != "end_turn")
            {
                w.BeginObject();
                w.Prop("kind", "armor");
                w.Prop("to", "player");
                w.Prop("delta", dblock);
                w.EndObject();
            }

            int dmax = after.PlayerMaxHp - before.PlayerMaxHp;
            if (dmax != 0)
            {
                w.BeginObject();
                w.Prop("kind", "max_hp");
                w.Prop("to", "player");
                w.Prop("delta", dmax);
                w.EndObject();
            }

            WriteLuck(w, "uncommon", after.LuckUncommon - before.LuckUncommon);
            WriteLuck(w, "rare", after.LuckRare - before.LuckRare);
            WriteLuck(w, "legendary", after.LuckLegendary - before.LuckLegendary);

            int de = after.PlayerEnergy - before.PlayerEnergy;
            if (de != 0)
            {
                w.BeginObject();
                w.Prop("kind", "energy");
                w.Prop("to", "player");
                w.Prop("delta", de);
                w.EndObject();
            }

            int dGold = after.Gold - before.Gold;
            if (dGold != 0)
            {
                w.BeginObject();
                w.Prop("kind", "gold");
                w.Prop("to", "player");
                w.Prop("delta", dGold);
                w.EndObject();
            }

            // 使用物品后「多出来一件东西」：HEAT POTION / CURSED POTION 这类
            // 会生成诅咒或道具的消耗品，delta 里只有 items_total/+1，看不出因果。
            // 只记「生成」，不记消耗——喝掉自己不该被当成正面效果。
            int dTotal = after.ItemsTotal - before.ItemsTotal;
            if (dTotal > 0)
            {
                w.BeginObject();
                w.Prop("kind", "items");
                w.Prop("field", "spawned");
                w.Prop("count", dTotal);
                w.EndObject();
            }

            // 进入整理阶段：诅咒类物品最明确的效果之一。
            // 只记「进入 3」，不记胜利画面 8 —— 否则每次击杀都会给攻击物品多算一个效果。
            if (after.InventoryPhase != before.InventoryPhase && after.InventoryPhase == 3)
            {
                w.BeginObject();
                w.Prop("kind", "phase");
                w.Prop("field", "inventory");
                w.Prop("from", before.InventoryPhase);
                w.Prop("to", after.InventoryPhase);
                w.EndObject();
            }

            WriteStatuses(w, "player", "", "", before.PlayerStatuses, after.PlayerStatuses, from);
        }

        private static void WriteLuck(JsonWriter w, string stat, float delta)
        {
            if (Math.Abs(delta) <= 0.0001f) return;
            w.BeginObject();
            w.Prop("kind", "luck");
            w.Prop("to", "player");
            w.Prop("stat", stat);
            w.Prop("delta", delta);
            w.EndObject();
        }

        // ---------------------------------------------------------------- enemies

        private static void WriteEnemies(JsonWriter w, Snapshot before, Snapshot after,
                                         string type, string itemName)
        {
            string from = type == "end_turn" ? "status" : "player";
            foreach (KeyValuePair<int, int> kv in before.EnemyHp)
            {
                int uid = kv.Key;
                string name = "";
                before.EnemyNames.TryGetValue(uid, out name);
                if (name == null) name = "";

                int hpAfter;
                int blockBefore = 0;
                int blockAfter = 0;
                before.EnemyBlock.TryGetValue(uid, out blockBefore);

                if (!after.EnemyHp.TryGetValue(uid, out hpAfter) ||
                    !after.EnemyBlock.ContainsKey(uid))
                {
                    // 敌人消失（死亡/逃跑）：实际打掉的就是剩余血量，不能算溢出伤害。
                    int dealt = Math.Max(0, kv.Value);
                    if (dealt > 0 || blockBefore > 0)
                        WriteEnemyDamage(w, from, uid, name, dealt, Math.Max(0, blockBefore), true);
                    continue;
                }

                after.EnemyBlock.TryGetValue(uid, out blockAfter);
                int dhp = hpAfter - kv.Value;
                int absorbed = Math.Max(0, blockBefore - blockAfter);

                if (dhp < 0 || absorbed > 0)
                {
                    WriteEnemyDamage(w, from, uid, name, Math.Max(0, -dhp), absorbed, false);
                }
                else if (dhp > 0)
                {
                    w.BeginObject();
                    w.Prop("kind", "heal");
                    w.Prop("from", type == "end_turn" ? "enemy" : from);
                    w.Prop("to", "enemy");
                    w.Prop("uid", "e" + uid);
                    if (name.Length > 0) w.Prop("name", name);
                    w.Prop("amount", dhp);
                    w.EndObject();
                }

                Dictionary<int, int> wasStatus;
                Dictionary<int, int> nowStatus;
                before.EnemyStatuses.TryGetValue(uid, out wasStatus);
                after.EnemyStatuses.TryGetValue(uid, out nowStatus);
                WriteStatuses(w, "enemy", "e" + uid, name, wasStatus, nowStatus,
                              type == "end_turn" ? "enemy" : from);
            }

            foreach (KeyValuePair<int, int> kv in after.EnemyHp)
            {
                if (before.EnemyHp.ContainsKey(kv.Key)) continue;
                string name = "";
                after.EnemyNames.TryGetValue(kv.Key, out name);
                w.BeginObject();
                w.Prop("kind", "spawn");
                w.Prop("to", "enemy");
                w.Prop("uid", "e" + kv.Key);
                if (name != null && name.Length > 0) w.Prop("name", name);
                w.Prop("hp", kv.Value);
                w.EndObject();
            }
        }

        private static void WriteEnemyDamage(JsonWriter w, string from, int uid, string name,
                                             int amount, int blocked, bool killed)
        {
            w.BeginObject();
            w.Prop("kind", "damage");
            w.Prop("from", from);
            w.Prop("to", "enemy");
            w.Prop("uid", "e" + uid);
            if (name.Length > 0) w.Prop("name", name);
            w.Prop("amount", amount);
            w.Prop("blocked", blocked);
            w.Prop("killed", killed);
            w.EndObject();
        }

        private static int SumAttack(Snapshot s)
        {
            int total = 0;
            foreach (KeyValuePair<int, int> kv in s.EnemyAttack)
                if (kv.Value > 0) total += kv.Value;
            return total;
        }

        // ---------------------------------------------------------------- statuses

        private static void WriteStatuses(JsonWriter w, string to, string uid, string name,
                                          Dictionary<int, int> before, Dictionary<int, int> after,
                                          string from)
        {
            if (after != null)
            {
                foreach (KeyValuePair<int, int> kv in after)
                {
                    int oldv = 0;
                    if (before != null) before.TryGetValue(kv.Key, out oldv);
                    int d = kv.Value - oldv;
                    if (d == 0) continue;
                    WriteStatusEffect(w, to, uid, name, from, kv.Key, d, kv.Value);
                }
            }
            if (before != null)
            {
                foreach (KeyValuePair<int, int> kv in before)
                {
                    if (after != null && after.ContainsKey(kv.Key)) continue;
                    WriteStatusEffect(w, to, uid, name, from, kv.Key, -kv.Value, 0);
                }
            }
        }

        private static void WriteStatusEffect(JsonWriter w, string to, string uid, string name,
                                              string from, int type, int delta, int value)
        {
            w.BeginObject();
            w.Prop("kind", "status");
            w.Prop("from", from);
            w.Prop("to", to);
            if (uid.Length > 0) w.Prop("uid", uid);
            if (name.Length > 0) w.Prop("name", name);
            w.Prop("status", Snapshot.StatusName(type));
            w.Prop("type", type);
            w.Prop("delta", delta);
            w.Prop("value", value);
            w.EndObject();
        }

        // ---------------------------------------------------------------- items

        private static void WriteItems(JsonWriter w, Snapshot before, Snapshot after)
        {
            foreach (KeyValuePair<int, Dictionary<int, int>> kv in before.ItemStatuses)
            {
                int uid = kv.Key;
                Dictionary<int, int> nowStatus;
                if (!after.ItemStatuses.TryGetValue(uid, out nowStatus)) continue;

                string name = "";
                before.ItemNames.TryGetValue(uid, out name);
                if (name == null) name = "";

                int oldCharge = 0;
                int nowCharge = 0;
                before.ItemCharges.TryGetValue(uid, out oldCharge);
                after.ItemCharges.TryGetValue(uid, out nowCharge);
                if (nowCharge != oldCharge)
                {
                    w.BeginObject();
                    w.Prop("kind", "charge");
                    w.Prop("item", name);
                    w.Prop("uid", "i" + uid);
                    w.Prop("delta", nowCharge - oldCharge);
                    w.Prop("value", nowCharge);
                    w.EndObject();
                }

                if (nowStatus != null)
                {
                    foreach (KeyValuePair<int, int> st in nowStatus)
                    {
                        int oldv = 0;
                        kv.Value.TryGetValue(st.Key, out oldv);
                        int d = st.Value - oldv;
                        if (d == 0) continue;
                        WriteItemStatusEffect(w, name, uid, st.Key, d, st.Value);
                    }
                }
                foreach (KeyValuePair<int, int> st in kv.Value)
                {
                    if (nowStatus != null && nowStatus.ContainsKey(st.Key)) continue;
                    WriteItemStatusEffect(w, name, uid, st.Key, -st.Value, 0);
                }
            }
        }

        private static void WriteItemStatusEffect(JsonWriter w, string name, int uid,
                                                  int type, int delta, int value)
        {
            w.BeginObject();
            w.Prop("kind", "item_status");
            w.Prop("item", name);
            w.Prop("uid", "i" + uid);
            w.Prop("status", Snapshot.ItemStatusName(type));
            w.Prop("type", type);
            w.Prop("delta", delta);
            w.Prop("value", value);
            w.EndObject();
        }

        // ---------------------------------------------------------------- utils

        private static int ParseUid(string uid)
        {
            if (string.IsNullOrEmpty(uid) || uid.Length < 2) return 0;
            int v;
            if (!int.TryParse(uid.Substring(1), out v)) return 0;
            return v;
        }
    }
}
