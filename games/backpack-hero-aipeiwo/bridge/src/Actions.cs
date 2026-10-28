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

        // ---- 地图 / 非战斗阶段 ----
        // 没有这几项，任何地图动作都会因为「Snapshot 里没有它」被判成没变化
        // (changed=false -> ok=false -> run.py 判卡死)。见 SPIKE-06 §7。
        public string RoomId = "";
        public int ViewingTop = -1;
        public int InventoryPhase = -1;
        public int Gold = -1;
        public int Level = -1;
        public int ItemsTotal = -1;
        public int ItemsPlaced = -1;
        public int ItemsInGrid = -1;
        public int ItemsLoose = -1;
        public int EventCount = -1;
        public int PopupCount = -1;

        // ---- 结构化回执用的「不可见效果」状态（SPIKE-07 §4 第二步） ----
        // 这些字段不进 state（state 由 StateReader 单独写），只用来算 ack.delta/receipt：
        // 状态效果、最大生命、幸运、物品自身状态（REVERSAL AMULET 这类「翻面」物品）。
        public float LuckUncommon = -1f;
        public float LuckRare = -1f;
        public float LuckLegendary = -1f;
        public Dictionary<int, int> PlayerStatuses = new Dictionary<int, int>();
        public Dictionary<int, string> EnemyNames = new Dictionary<int, string>();
        public Dictionary<int, int> EnemyMaxHp = new Dictionary<int, int>();
        public Dictionary<int, int> EnemyAttack = new Dictionary<int, int>();
        public Dictionary<int, Dictionary<int, int>> EnemyStatuses =
            new Dictionary<int, Dictionary<int, int>>();
        public Dictionary<int, string> ItemNames = new Dictionary<int, string>();
        public Dictionary<int, int> ItemCharges = new Dictionary<int, int>();
        public Dictionary<int, Dictionary<int, int>> ItemStatuses =
            new Dictionary<int, Dictionary<int, int>>();

        private static int SI(Func<int> f, int d)
        {
            try { return f(); } catch { return d; }
        }

        private static float SF(Func<float> f, float d)
        {
            try { return f(); } catch { return d; }
        }

        private static string SS(Func<string> f)
        {
            try { string v = f(); return v == null ? "" : v; } catch { return ""; }
        }

        /// <summary>状态效果 → type:value 的有序映射（同名状态相加）。</summary>
        public static Dictionary<int, int> ReadStatus(Status st)
        {
            Dictionary<int, int> map = new Dictionary<int, int>();
            if (st == null) return map;
            List<StatusEffect> list = null;
            try { list = st.GetStatusEffects(); } catch { }
            if (list == null) return map;
            for (int i = 0; i < list.Count; i++)
            {
                StatusEffect se = list[i];
                if (se == null) continue;
                int t = SI(delegate { return (int)se.type; }, -1);
                if (t < 0) continue;
                int v = SI(delegate { return se.value; }, 0);
                if (map.ContainsKey(t)) map[t] = map[t] + v;
                else map[t] = v;
            }
            return map;
        }

        /// <summary>物品自身的状态效果（Item2.ItemStatusEffect）→ type:num。</summary>
        public static Dictionary<int, int> ReadItemStatus(Item2 it)
        {
            Dictionary<int, int> map = new Dictionary<int, int>();
            if (it == null) return map;
            List<Item2.ItemStatusEffect> list = null;
            try { list = it.activeItemStatusEffects; } catch { }
            if (list == null) return map;
            for (int i = 0; i < list.Count; i++)
            {
                Item2.ItemStatusEffect se = list[i];
                if (se == null) continue;
                int t = SI(delegate { return (int)se.type; }, -1);
                if (t < 0) continue;
                int v = SI(delegate { return se.num; }, 0);
                if (map.ContainsKey(t)) map[t] = map[t] + v;
                else map[t] = v;
            }
            return map;
        }

        /// <summary>枚举名给回执用；枚举取不到时退回数字，不能让整条回执失败。</summary>
        public static string StatusName(int t)
        {
            try { return ((StatusEffect.Type)t).ToString(); }
            catch { return "STATUS_" + t; }
        }

        public static string ItemStatusName(int t)
        {
            try { return ((Item2.ItemStatusEffect.Type)t).ToString(); }
            catch { return "ITEM_STATUS_" + t; }
        }

        public static bool SameStatus(Dictionary<int, int> a, Dictionary<int, int> b)
        {
            if (a == null || b == null) return a == b;
            if (a.Count != b.Count) return false;
            foreach (KeyValuePair<int, int> kv in a)
            {
                int v;
                if (!b.TryGetValue(kv.Key, out v)) return false;
                if (v != kv.Value) return false;
            }
            return true;
        }

        /// <summary>把「状态 type→value」的差异写成 {BURN: +2, ...}。只有变化的项。</summary>
        public static void WriteStatusDelta(JsonWriter w, Dictionary<int, int> before,
                                            Dictionary<int, int> after, bool itemStatus)
        {
            if (after != null)
            {
                foreach (KeyValuePair<int, int> kv in after)
                {
                    int oldv = 0;
                    if (before != null) before.TryGetValue(kv.Key, out oldv);
                    int d = kv.Value - oldv;
                    if (d == 0) continue;
                    w.Prop(itemStatus ? ItemStatusName(kv.Key) : StatusName(kv.Key), d);
                }
            }
            if (before != null)
            {
                foreach (KeyValuePair<int, int> kv in before)
                {
                    if (after != null && after.ContainsKey(kv.Key)) continue;
                    w.Prop(itemStatus ? ItemStatusName(kv.Key) : StatusName(kv.Key), -kv.Value);
                }
            }
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
            s.PlayerStatuses = ReadStatus(pst);
            s.LuckUncommon = pl == null ? -1f : SF(delegate { return pl.uncommonLuck + pl.uncommonLuckFromItems; }, -1f);
            s.LuckRare = pl == null ? -1f : SF(delegate { return pl.rareLuck + pl.rareLuckFromItems; }, -1f);
            s.LuckLegendary = pl == null ? -1f : SF(delegate { return pl.legendaryLuck + pl.legendaryLuckFromItems; }, -1f);
            s.BattlePhase = gf == null ? -1 : SI(delegate { return (int)gf.battlePhase; }, -1);
            s.TurnNumber = gf == null ? -1 : SI(delegate { return gf.turnNumber; }, -1);
            s.Floor = gm == null ? -1 : SI(delegate { return gm.floor; }, -1);

            // ---- 地图 / UI ----
            s.RoomId = World.PlayerRoomUid();
            s.ViewingTop = gm == null ? -1 : SI(delegate { return (int)gm.viewingTop; }, -1);
            s.InventoryPhase = gm == null ? -1 : SI(delegate { return (int)gm.inventoryPhase; }, -1);
            s.Gold = gm == null ? -1 : SI(delegate { return gm.goldAmount; }, -1);
            s.EventCount = World.CountEvents();
            s.PopupCount = World.CountPopups();

            try
            {
                if (pl != null) s.Level = SI(delegate { return pl.level; }, -1);
            }
            catch { }

            try
            {
                List<Item2> items = Item2.allItems;
                if (items != null)
                {
                    int total = 0;
                    int placed = 0;
                    for (int i = 0; i < items.Count; i++)
                    {
                        Item2 it = items[i];
                        if (it == null) continue;
                        total++;
                        if (World.IsPlaced(it))
                        {
                            placed++;
                            int iid = SI(delegate { return it.GetInstanceID(); }, 0);
                            s.ItemNames[iid] = SS(delegate { return it.displayName; });
                            s.ItemCharges[iid] = SI(delegate { return it.currentCharges; }, -1);
                            s.ItemStatuses[iid] = ReadItemStatus(it);
                        }
                    }
                    s.ItemsTotal = total;
                    s.ItemsPlaced = placed;
                }
            }
            catch { }

            s.ItemsInGrid = World.CountInGrid();
            s.ItemsLoose = World.CountLoose();

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
                        s.EnemyNames[id] = SS(delegate { return e.displayName; });
                        s.EnemyMaxHp[id] = st == null ? -1 : SI(delegate { return st.maxHealth; }, -1);
                        s.EnemyStatuses[id] = ReadStatus(st);

                        int atk = 0;
                        try
                        {
                            Enemy.PossibleAttack pa = e.myNextAttack;
                            List<Enemy.Attack> atks = pa == null ? null : pa.attacks;
                            if (atks != null)
                            {
                                for (int k = 0; k < atks.Count; k++)
                                {
                                    Enemy.Attack a = atks[k];
                                    if (a == null) continue;
                                    if (SI(delegate { return (int)a.type; }, -1) != 0) continue;
                                    if (SI(delegate { return a.hideIntention ? 1 : 0; }, 0) == 1) continue;
                                    int p = SI(delegate { return a.currentPower; }, 0);
                                    if (p > 0) atk += p;
                                }
                            }
                        }
                        catch { }
                        s.EnemyAttack[id] = atk;
                    }
                }
            }
            catch { }

            return s;
        }

        /// <summary>
        /// 到底有没有变。**判断必须走这里，不能靠 DiffJson() != "{}"**——
        /// 早先版本 DiffJson 无条件写 "enemies":{}，导致每个动作都被判成有变化。
        /// </summary>
        public bool Differs(Snapshot a)
        {
            if (a == null) return true;
            if (a.PlayerHp != PlayerHp) return true;
            if (a.PlayerMaxHp != PlayerMaxHp) return true;
            if (a.PlayerEnergy != PlayerEnergy) return true;
            if (a.PlayerBlock != PlayerBlock) return true;
            if (!SameStatus(PlayerStatuses, a.PlayerStatuses)) return true;
            if (Math.Abs(a.LuckUncommon - LuckUncommon) > 0.0001f) return true;
            if (Math.Abs(a.LuckRare - LuckRare) > 0.0001f) return true;
            if (Math.Abs(a.LuckLegendary - LuckLegendary) > 0.0001f) return true;
            if (a.BattlePhase != BattlePhase) return true;
            if (a.TurnNumber != TurnNumber) return true;
            if (a.Floor != Floor) return true;
            if (a.RoomId != RoomId) return true;
            if (a.ViewingTop != ViewingTop) return true;
            if (a.InventoryPhase != InventoryPhase) return true;
            if (a.Gold != Gold) return true;
            if (a.Level != Level) return true;
            if (a.ItemsTotal != ItemsTotal) return true;
            if (a.ItemsPlaced != ItemsPlaced) return true;
            if (a.ItemsInGrid != ItemsInGrid) return true;
            if (a.ItemsLoose != ItemsLoose) return true;
            if (a.EventCount != EventCount) return true;
            if (a.PopupCount != PopupCount) return true;
            if (a.EnemyHp.Count != EnemyHp.Count) return true;
            foreach (KeyValuePair<int, int> kv in EnemyHp)
            {
                int now;
                if (!a.EnemyHp.TryGetValue(kv.Key, out now)) return true;
                if (now != kv.Value) return true;
                int nowBlock;
                if (a.EnemyBlock.TryGetValue(kv.Key, out nowBlock))
                {
                    int wasBlock;
                    EnemyBlock.TryGetValue(kv.Key, out wasBlock);
                    if (nowBlock != wasBlock) return true;
                }
                else if (EnemyBlock.ContainsKey(kv.Key))
                {
                    return true;
                }

                Dictionary<int, int> nowStatus;
                if (a.EnemyStatuses.TryGetValue(kv.Key, out nowStatus))
                {
                    Dictionary<int, int> wasStatus;
                    EnemyStatuses.TryGetValue(kv.Key, out wasStatus);
                    if (!SameStatus(wasStatus, nowStatus)) return true;
                }
                else if (EnemyStatuses.ContainsKey(kv.Key))
                {
                    return true;
                }
            }

            // 物品自身状态 / 充能：REVERSAL AMULET、充能类物品靠这一项才「有变化」。
            foreach (KeyValuePair<int, Dictionary<int, int>> kv in ItemStatuses)
            {
                Dictionary<int, int> now;
                if (!a.ItemStatuses.TryGetValue(kv.Key, out now)) return true;
                if (!SameStatus(kv.Value, now)) return true;
            }
            foreach (KeyValuePair<int, int> kv in ItemCharges)
            {
                int now;
                if (!a.ItemCharges.TryGetValue(kv.Key, out now)) return true;
                if (now != kv.Value) return true;
            }
            return false;
        }

        /// <summary>算出 after 相对 this 的变化。没有变化就是空对象 {}。</summary>
        public string DiffJson(Snapshot after)
        {
            JsonWriter w = new JsonWriter();
            w.BeginObject();

            if (after.PlayerHp != PlayerHp) w.Prop("player_hp", after.PlayerHp - PlayerHp);
            if (after.PlayerMaxHp != PlayerMaxHp) w.Prop("player_max_hp", after.PlayerMaxHp - PlayerMaxHp);
            if (after.PlayerEnergy != PlayerEnergy) w.Prop("player_energy", after.PlayerEnergy - PlayerEnergy);
            if (after.PlayerBlock != PlayerBlock) w.Prop("player_block", after.PlayerBlock - PlayerBlock);
            if (Math.Abs(after.LuckUncommon - LuckUncommon) > 0.0001f)
                w.Prop("luck_uncommon", after.LuckUncommon - LuckUncommon);
            if (Math.Abs(after.LuckRare - LuckRare) > 0.0001f)
                w.Prop("luck_rare", after.LuckRare - LuckRare);
            if (Math.Abs(after.LuckLegendary - LuckLegendary) > 0.0001f)
                w.Prop("luck_legendary", after.LuckLegendary - LuckLegendary);
            if (!SameStatus(PlayerStatuses, after.PlayerStatuses))
            {
                w.Name("player_statuses").BeginObject();
                WriteStatusDelta(w, PlayerStatuses, after.PlayerStatuses, false);
                w.EndObject();
            }

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

            // ---- 地图 / 非战斗 ----
            if (after.RoomId != RoomId)
            {
                w.Name("room").BeginArray().Value(RoomId).Value(after.RoomId).EndArray();
            }
            if (after.ViewingTop != ViewingTop)
            {
                w.Name("viewing_top").BeginArray().Value(ViewingTop).Value(after.ViewingTop).EndArray();
            }
            if (after.InventoryPhase != InventoryPhase)
            {
                w.Name("inventory_phase").BeginArray().Value(InventoryPhase).Value(after.InventoryPhase).EndArray();
            }
            if (after.Gold != Gold) w.Prop("gold", after.Gold - Gold);
            if (after.Level != Level)
            {
                w.Name("level").BeginArray().Value(Level).Value(after.Level).EndArray();
            }
            if (after.ItemsTotal != ItemsTotal) w.Prop("items_total", after.ItemsTotal - ItemsTotal);
            if (after.ItemsPlaced != ItemsPlaced) w.Prop("items_placed", after.ItemsPlaced - ItemsPlaced);
            if (after.ItemsInGrid != ItemsInGrid) w.Prop("items_in_grid", after.ItemsInGrid - ItemsInGrid);
            if (after.ItemsLoose != ItemsLoose) w.Prop("items_loose", after.ItemsLoose - ItemsLoose);
            if (after.EventCount != EventCount) w.Prop("events", after.EventCount - EventCount);
            if (after.PopupCount != PopupCount) w.Prop("popups", after.PopupCount - PopupCount);

            // 先收集，非空才写，避免永远吐一个 "enemies":{}
            List<string> keys = new List<string>();
            List<object> vals = new List<object>();
            foreach (KeyValuePair<int, int> kv in EnemyHp)
            {
                int now;
                if (!after.EnemyHp.TryGetValue(kv.Key, out now))
                {
                    keys.Add("e" + kv.Key);
                    vals.Add("gone");
                    continue;
                }
                if (now != kv.Value)
                {
                    keys.Add("e" + kv.Key);
                    vals.Add(now - kv.Value);
                }
            }
            foreach (KeyValuePair<int, int> kv in after.EnemyHp)
            {
                if (!EnemyHp.ContainsKey(kv.Key))
                {
                    keys.Add("e" + kv.Key);
                    vals.Add("appeared");
                }
            }

            if (keys.Count > 0)
            {
                w.Name("enemies").BeginObject();
                for (int i = 0; i < keys.Count; i++)
                {
                    w.Name(keys[i]);
                    if (vals[i] is int) w.Value((int)vals[i]);
                    else w.Value((string)vals[i]);
                }
                w.EndObject();
            }

            // 护甲变化单独一组：打中但被格挡时 hp 不变，只有 block 会动。
            // 不输出这个，agent 会以为「这刀什么也没发生」（SPIKE-04 实测踩到）。
            List<string> bkeys = new List<string>();
            List<int> bvals = new List<int>();
            foreach (KeyValuePair<int, int> kv in EnemyBlock)
            {
                int nowBlock;
                if (!after.EnemyBlock.TryGetValue(kv.Key, out nowBlock)) continue;
                if (nowBlock != kv.Value)
                {
                    bkeys.Add("e" + kv.Key);
                    bvals.Add(nowBlock - kv.Value);
                }
            }
            if (bkeys.Count > 0)
            {
                w.Name("enemies_block").BeginObject();
                for (int i = 0; i < bkeys.Count; i++) w.Prop(bkeys[i], bvals[i]);
                w.EndObject();
            }

            // 状态效果变化：毒/燃烧/力量/幸运……这些不改变 hp/block，但确实是动作效果。
            List<int> stKeys = new List<int>();
            foreach (KeyValuePair<int, Dictionary<int, int>> kv in EnemyStatuses)
            {
                Dictionary<int, int> now;
                if (!after.EnemyStatuses.TryGetValue(kv.Key, out now)) continue;
                if (!SameStatus(kv.Value, now)) stKeys.Add(kv.Key);
            }
            if (stKeys.Count > 0)
            {
                w.Name("enemy_statuses").BeginObject();
                for (int i = 0; i < stKeys.Count; i++)
                {
                    int uid = stKeys[i];
                    w.Name("e" + uid).BeginObject();
                    Dictionary<int, int> was;
                    Dictionary<int, int> now;
                    EnemyStatuses.TryGetValue(uid, out was);
                    after.EnemyStatuses.TryGetValue(uid, out now);
                    WriteStatusDelta(w, was, now, false);
                    w.EndObject();
                }
                w.EndObject();
            }

            // 物品自身状态 / 充能：REVERSAL AMULET 这类「翻面」物品只有这里看得见。
            List<int> itemKeys = new List<int>();
            foreach (KeyValuePair<int, Dictionary<int, int>> kv in ItemStatuses)
            {
                Dictionary<int, int> now;
                if (!after.ItemStatuses.TryGetValue(kv.Key, out now)) continue;
                int nowCharge;
                int oldCharge;
                ItemCharges.TryGetValue(kv.Key, out oldCharge);
                after.ItemCharges.TryGetValue(kv.Key, out nowCharge);
                if (!SameStatus(kv.Value, now) || nowCharge != oldCharge) itemKeys.Add(kv.Key);
            }
            if (itemKeys.Count > 0)
            {
                w.Name("items_state").BeginObject();
                for (int i = 0; i < itemKeys.Count; i++)
                {
                    int uid = itemKeys[i];
                    w.Name("i" + uid).BeginObject();
                    Dictionary<int, int> was;
                    Dictionary<int, int> now;
                    ItemStatuses.TryGetValue(uid, out was);
                    after.ItemStatuses.TryGetValue(uid, out now);
                    WriteStatusDelta(w, was, now, true);
                    int oldCharge;
                    int nowCharge;
                    ItemCharges.TryGetValue(uid, out oldCharge);
                    after.ItemCharges.TryGetValue(uid, out nowCharge);
                    if (nowCharge != oldCharge) w.Prop("charges", nowCharge - oldCharge);
                    w.EndObject();
                }
                w.EndObject();
            }

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

        /// <summary>只读诊断动作（diag_event / open_chest）的 JSON，直接进 ack.diag。</summary>
        public string DiagJson;

        /// <summary>
        /// 结束回合要等敌人打完整轮：光靠「状态 N 毫秒没变」会在动画间隙误判，
        /// 所以额外要求 battlePhase 回到 playerTurn(1) 或 outOfBattle(0)。
        /// </summary>
        public bool WaitPlayerTurn;

        /// <summary>整理阶段：点完「结束整理」要等 inventoryPhase 退出 specialReorganization。</summary>
        public bool WaitReorg;

        /// <summary>move_to_room：等玩家真的到达这个房间（"r123"）才算完成。</summary>
        public string WaitRoom = "";

        /// <summary>open_map：等 viewingTop 变成这个值（1=map）才算完成。</summary>
        public int WaitViewingTop = -1;

        /// <summary>close_popup：等弹窗数量归零才算完成。</summary>
        public bool WaitNoPopup;

        /// <summary>
        /// enter_door：出门会先播一段走路/演出，state 可能静止好几秒，
        /// 光靠 settle 会在演出中途就回执（实测 500ms 就返回了，切层还没发生）。
        /// 所以要求「世界签名变了」才算完成。
        /// </summary>
        public bool WaitWorldChange;
        public string WorldSig = "";

        /// <summary>place_item：等背包里的物品数真的变多（否则只是「拖了一下」）。</summary>
        public bool WaitItemsInGrid;
        public int ItemsInGridBefore = -1;

        /// <summary>open_chest：等地上的战利品真的出现（宝箱可能给 0 件，但正常会变多）。</summary>
        public bool WaitLooseAbove;
        public int LooseBefore = -1;

        /// <summary>finish_level_up：等升级界面真的关掉（levelingUp == false）。</summary>
        public bool WaitNoLeveling;

        /// <summary>start_run：等真的进局（DungeonPlayer 出现）。</summary>
        public bool WaitDungeon;
    }

    internal static class Actions
    {
        public const float SettleSeconds = 0.4f;

        /// <summary>
        /// 格子是否真的空着。`containsItem` 在实测里会滞后（放了 4 件仍报 9 空），
        /// 所以再问一次游戏自己的 `TestForItem()`（Physics2D 找 tag=Item 且 inGrid）。
        /// </summary>
        public static bool SquareOpen(GridSquare gs)
        {
            if (gs == null) return false;
            try { if (gs.isPouch) return false; } catch { }
            try { if (gs.containsItem) return false; } catch { }
            object hit = World.CallPrivate(gs, "TestForItem", null);
            if (hit is bool && (bool)hit) return false;
            return true;
        }

        /// <summary>place_item 的轮转游标：连续放多件战利品时不要都塞进第一个空格。</summary>
        private static int _placeCursor = 0;

        /// <summary>当前是否处在「等玩家操作」或「不在战斗」的稳定阶段。</summary>
        public static bool PhaseIsPlayerOrIdle()
        {
            try
            {
                // 死了也算「可以收尾」：血量归零后 battlePhase 会停在 3，
                // 不认这条 end_turn 会白等 20 秒（实测踩到）。
                Player pDead = Player.main;
                if (pDead != null)
                {
                    Status stDead = pDead.stats;
                    if (stDead != null && stDead.health <= 0) return true;
                }

                // 整理阶段（污染/诅咒）也算「可以收尾」：实测敌人回合里掉出诅咒会
                // 直接把 inventoryPhase 顶到 3，此时 battlePhase 还停在 3，
                // 旧写法会干等 15 秒然后报假超时（SPIKE-06 §21）。
                GameManager gm = GameManager.main;
                if (gm != null && (int)gm.inventoryPhase == 3) return true;

                GameFlowManager gf = GameFlowManager.main;
                if (gf == null) return true;
                int p = (int)gf.battlePhase;
                return p == 0 || p == 1;
            }
            catch { return true; }
        }

        /// <summary>整理阶段是否已经退出（inventoryPhase != specialReorganization(3)）。</summary>
        public static bool ReorganizeFinished()
        {
            try
            {
                GameManager gm = GameManager.main;
                if (gm == null) return true;
                return (int)gm.inventoryPhase != 3;
            }
            catch { return true; }
        }

        /// <summary>move_to_room 的阶段门：玩家停下来了，而且最近房间就是目标。</summary>
        public static bool RoomArrived(string uid)
        {
            if (uid == null || uid.Length == 0) return true;
            try
            {
                DungeonPlayer dp = DungeonPlayer.main;
                if (dp == null) return true;
                if (dp.isMoving) return false;
                return World.PlayerRoomUid() == uid;
            }
            catch { return true; }
        }

        /// <summary>open_map 的阶段门：viewingTop 已经是目标值。</summary>
        public static bool ViewingTopIs(int want)
        {
            try
            {
                GameManager gm = GameManager.main;
                if (gm == null) return true;
                return (int)gm.viewingTop == want;
            }
            catch { return true; }
        }

        /// <summary>close_popup 的阶段门：场上没有弹窗了。</summary>
        public static bool NoPopups()
        {
            int n = World.CountPopups();
            return n <= 0;
        }

        /// <summary>enter_door 的阶段门：世界签名变了（换层/换房间/事件数变了）。</summary>
        public static bool WorldChanged(string sig)
        {
            if (sig == null || sig.Length == 0) return true;
            return World.Signature() != sig;
        }

        /// <summary>place_item 的阶段门：背包里的物品数比之前多了。</summary>
        public static bool ItemsInGridAbove(int before)
        {
            return World.CountInGrid() > before;
        }

        /// <summary>open_chest 的阶段门：地上的散落物比以前多（战利品出现了）。</summary>
        public static bool LooseItemsAbove(int before)
        {
            return World.CountLoose() > before;
        }

        /// <summary>
        /// open_chest 的完成条件：战利品出现，**或者** 开出了伪装宝箱（直接进战斗）。
        /// 只有 loose 会漏掉 mimic；只有 battle 会漏掉普通宝箱。
        /// </summary>
        public static bool LooseItemsOrCombat(int looseBefore, int battleBefore)
        {
            if (World.CountLoose() > looseBefore) return true;
            try
            {
                GameFlowManager gf = GameFlowManager.main;
                if (gf != null)
                {
                    int p = (int)gf.battlePhase;
                    if (p != 0 && p != battleBefore) return true;
                }
            }
            catch { }
            return false;
        }

        /// <summary>
        /// 世界是否已经稳定：不在走位、不在过场（`DungeonPlayer.locked`）。
        /// 实测 `enter_door` 只等「floor 变了」会在新层还没加载完时就回执
        /// （那一刻 rooms 还是上一层的 6 个，locked=true）。
        /// </summary>
        public static bool WorldReady()
        {
            try
            {
                DungeonPlayer dp = DungeonPlayer.main;
                if (dp == null) return true;
                if (dp.isMoving) return false;
                if (dp.locked) return false;
                return true;
            }
            catch { return true; }
        }

        /// <summary>start_run 的阶段门：地牢对象已经建出来了（说明真的进局了）。</summary>
        public static bool DungeonReady()
        {
            try { return DungeonPlayer.main != null; } catch { return false; }
        }

        /// <summary>升级界面是否已经关掉。</summary>
        public static bool LevelingDone()
        {
            try
            {
                LevelUpManager lum = LevelUpManager.main;
                if (lum == null) return true;
                return !lum.levelingUp;
            }
            catch { return true; }
        }

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

            // 整理阶段（中污染/诅咒时进入）：摆好后必须点「结束整理」才继续。
            // GameManager 上有 finishReorganizingButton，onClick 绑的就是这个方法。
            if (type == "finish_reorganize")
            {
                GameManager gm = GameManager.main;
                if (gm == null) throw new InvalidOperationException("GameManager.main is null");
                gm.FinishReorganizeButton();
                return;
            }

            if (type == "skip_reorganize")
            {
                GameManager gm = GameManager.main;
                if (gm == null) throw new InvalidOperationException("GameManager.main is null");
                gm.SkipReorganizeButton();
                return;
            }

            // ---- 地图 / 非战斗动作（SPIKE-06） ----
            // 这四个都是「游戏自己的入口」，不是模拟点击：
            //   open_map    -> GameManager.ShowMap()
            //   move_to_room-> DungeonPlayer.PathFromPlayerToRoom()
            //   start_event -> DungeonPlayer.StartEvent()（当前房间里的 DungeonEvent）
            //   level_up    -> LevelUpManager.LevelUpButton()
            //   close_popup -> BasicPopUp.Close()
            if (type == "open_map")
            {
                GameManager gmMap = GameManager.main;
                if (gmMap == null) throw new InvalidOperationException("GameManager.main is null");
                bool force = MiniJson.Bool(pc.Action, "force", false);
                gmMap.ShowMap(force);
                return;
            }

            if (type == "move_to_room")
            {
                string roomUid = MiniJson.Str(pc.Action, "room", "");
                DungeonRoom room = FindRoom(roomUid);
                if (room == null) throw new InvalidOperationException("room not found: " + roomUid);
                DungeonPlayer dpMove = DungeonPlayer.main;
                if (dpMove == null) throw new InvalidOperationException("DungeonPlayer.main is null");

                // 实测：PathFromPlayerToRoom 单独调用**没有任何效果**（20s 超时、state 一格没动）。
                // 玩家点房间走的是 DungeonRoom.OnPressStart -> MoveToRoom()。
                // 所以给 via 留四个口子，方便不重新部署就能试。
                string via = MiniJson.Str(pc.Action, "via", "room");
                if (via == "path") dpMove.PathFromPlayerToRoom(room);
                else if (via == "closest") dpMove.MoveToClosestRoom();
                else if (via == "press") room.OnPressStart("Mouse0", true);
                else if (via == "consider")
                {
                    // 模拟「鼠标停在房间上」再走：ConsiderRoom -> MoveToClosestRoom
                    dpMove.ConsiderRoom(room.transform.position);
                    dpMove.MoveToClosestRoom();
                }
                else room.MoveToRoom();
                return;
            }

            if (type == "start_event")
            {
                DungeonPlayer dpEvent = DungeonPlayer.main;
                if (dpEvent == null) throw new InvalidOperationException("DungeonPlayer.main is null");
                dpEvent.StartEvent();
                return;
            }

            // 只读诊断：把「当前房间 / 相关事件 / itemsToSpawn / open 属性 / Chest 绑定」
            // 一次全打出来。ack.diag 里可直接读，不需要改 state schema。
            if (type == "diag_event")
            {
                DiagEventAction(pc);
                return;
            }

            // 宝箱：StartEvent 对 Chest 是 no-op（真机 + IL 都确认），真正入口是 Chest.OpenChest()。
            // 如果 Chest.dungeonEvent 没绑上，先把当前房间的 Chest DungeonEvent 绑给它。
            if (type == "open_chest")
            {
                OpenChestAction(pc);
                return;
            }

            // 关地图。`GameManager` 只有 ShowMap(force)，开着的时候怎么调都不关
            // （实测 force=true/false 都是空操作），所以只能走真正的输入处理器：
            // `mapInputHandler` 是 nonpublic 字段，但 `LaunchCustomEvent()` 是 public。
            // 这是解开「地图开着时 StartEvent 无效 → 宝箱开不了 → 房间来回走」的关键。
            if (type == "close_map")
            {
                GameManager gmMapClose = GameManager.main;
                if (gmMapClose == null) throw new InvalidOperationException("GameManager.main is null");

                // 第一级：官方输入处理器（实测在「已开着」时是空操作，留着当兜底）
                InputHandler ih = World.Field(gmMapClose, "mapInputHandler") as InputHandler;
                if (ih != null)
                {
                    try { ih.LaunchCustomEvent(); } catch { }
                }

                // 第二级：地图按钮自己的 onClick
                if ((int)gmMapClose.viewingTop == (int)GameManager.ViewingTop.map)
                {
                    UnityEngine.GameObject mb = World.Field(gmMapClose, "mapButton") as UnityEngine.GameObject;
                    if (mb != null)
                    {
                        UnityEngine.UI.Button btn = mb.GetComponent<UnityEngine.UI.Button>();
                        if (btn != null) btn.onClick.Invoke();
                        else mb.SendMessage("OnMouseUpAsButton", SendMessageOptions.DontRequireReceiver);
                    }
                }

                // 第三级：直接置位。`viewingTop` 是 public 字段（枚举 inventory=0 / map=1），
                // 而 StartEvent 正是被这个状态挡住的——所以哪怕地图的视觉还在，
                // 拨回 inventory 也能让事件交互恢复。这是唯一有明确机制的一条。
                if ((int)gmMapClose.viewingTop == (int)GameManager.ViewingTop.map)
                {
                    gmMapClose.viewingTop = GameManager.ViewingTop.inventory;
                    try { gmMapClose.ShowBackpackAndMapButtons(); } catch { }
                }
                return;
            }

            // 战后奖励 / 事件里的「N 选 1」：SelectItemDialogue.ChooseItem(GameObject)。
            // 候选挂在 nonpublic 的 itemParent 下，按 child 顺序取。
            if (type == "pick_dialogue")
            {
                UnityEngine.Object[] dlgObjs =
                    UnityEngine.Object.FindObjectsOfType(typeof(SelectItemDialogue));
                if (dlgObjs == null || dlgObjs.Length == 0)
                    throw new InvalidOperationException("no SelectItemDialogue");
                SelectItemDialogue dlg = dlgObjs[0] as SelectItemDialogue;
                if (dlg == null) throw new InvalidOperationException("dialogue is not SelectItemDialogue");
                Transform parent = World.Field(dlg, "itemParent") as Transform;
                if (parent == null) throw new InvalidOperationException("itemParent not found");
                int want = (int)MiniJson.Num(pc.Action, "index", 0);
                int n = parent.childCount;
                if (n <= 0) throw new InvalidOperationException("dialogue has no items");
                if (want < 0) want = 0;
                if (want >= n) want = n - 1;
                dlg.ChooseItem(parent.GetChild(want).gameObject);
                return;
            }

            // 关掉「N 选 1」对话框（可跳过的那种）。
            if (type == "end_dialogue")
            {
                UnityEngine.Object[] dlgObjs2 =
                    UnityEngine.Object.FindObjectsOfType(typeof(SelectItemDialogue));
                if (dlgObjs2 == null || dlgObjs2.Length == 0)
                    throw new InvalidOperationException("no SelectItemDialogue");
                SelectItemDialogue dlg2 = dlgObjs2[0] as SelectItemDialogue;
                dlg2.EndEvent();
                return;
            }

            // 死后重开一局。桥接原本没有任何「回主菜单」的动作，
            // 死亡界面又 popups=0 / legal=[] / MenuManager.main=null，
            // 所以每局之间必须人工重启游戏。Retry() 是 GameManager 的 public 方法。
            if (type == "retry_run")
            {
                GameManager gmRetry = GameManager.main;
                if (gmRetry == null) throw new InvalidOperationException("GameManager.main is null");
                gmRetry.Retry();
                return;
            }

            // 升级选格子：优先点第 index 个格子的按钮，没有 Button 就发点击消息。
            // `Select()` 是无参的盲选，想「挑哪个」只能靠这个。
            if (type == "levelup_pick")
            {
                LevelUpManager lumPick = LevelUpManager.main;
                if (lumPick == null) throw new InvalidOperationException("LevelUpManager.main is null");
                Transform gp = World.Field(lumPick, "gridParent") as Transform;
                if (gp == null) throw new InvalidOperationException("gridParent not found");
                int wantIdx = (int)MiniJson.Num(pc.Action, "index", 0);
                int count = gp.childCount;
                if (count <= 0) throw new InvalidOperationException("gridParent has no children");
                if (wantIdx < 0) wantIdx = 0;
                if (wantIdx >= count) wantIdx = count - 1;
                GameObject cell = gp.GetChild(wantIdx).gameObject;
                UnityEngine.UI.Button cellBtn = cell.GetComponent<UnityEngine.UI.Button>();
                if (cellBtn != null) cellBtn.onClick.Invoke();
                else cell.SendMessage("OnMouseUpAsButton", SendMessageOptions.DontRequireReceiver);
                return;
            }

            // Exit（出口/传送门）不是靠 StartEvent 触发的（实测无效），
            // 走的是 Door 组件：OnMouseUpAsButton -> ConsiderWalk -> StartWalk -> WalkToDoor。
            if (type == "enter_door")
            {
                Door door = FindDoor(MiniJson.Str(pc.Action, "door", ""));
                if (door == null) throw new InvalidOperationException("door not found");
                string dvia = MiniJson.Str(pc.Action, "via", "click");
                // ConsiderWalk / StartWalk 都是 private；public 的入口只有这三个。
                if (dvia == "walk") MelonCoroutines.Start(door.WalkToDoor());
                else if (dvia == "press") door.OnPressStart("Mouse0", true);
                else door.OnMouseUpAsButton();
                return;
            }

            // 有些事件只需要「结束」：DungeonEvent.FinishEvent()
            if (type == "finish_event")
            {
                DungeonPlayer dpFin = DungeonPlayer.main;
                if (dpFin == null) throw new InvalidOperationException("DungeonPlayer.main is null");
                DungeonEvent evFin = null;
                try { evFin = dpFin.dungeonEvent; } catch { }
                if (evFin == null) throw new InvalidOperationException("no event in current room");
                evFin.FinishEvent();
                return;
            }

            if (type == "level_up")
            {
                LevelUpManager lum = LevelUpManager.main;
                if (lum == null) throw new InvalidOperationException("LevelUpManager.main is null");
                lum.LevelUpButton();
                return;
            }

            // 开局：主菜单的按钮就是 MenuManager 的 public 方法，
            // 不用模拟鼠标（实测这个环境下鼠标点到哪儿都不可靠）。
            //   via=adventure -> MenuManager.GoAdventuring()   「快速开始」
            //   via=story     -> MenuManager.StartGameButton()  「故事模式」
            //   via=continue  -> MenuManager.LoadGameAndContinueSave()
            //   via=confirm   -> NewCharacterSelector.StartGame() 「去冒险吧」
            //   via=auto      -> 有菜单就点快速开始，否则点去冒险吧
            if (type == "start_run")
            {
                string srVia = MiniJson.Str(pc.Action, "via", "auto");

                MenuManager mm = null;
                try { mm = MenuManager.main; } catch { }

                NewCharacterSelector sel = null;
                try
                {
                    UnityEngine.Object[] objs =
                        UnityEngine.Object.FindObjectsOfType(typeof(NewCharacterSelector));
                    if (objs != null && objs.Length > 0) sel = objs[0] as NewCharacterSelector;
                }
                catch { }

                if (srVia == "quickstart" || srVia == "story")
                {
                    if (mm == null) throw new InvalidOperationException("MenuManager.main is null");
                    mm.StartGameButton();
                    return;
                }
                if (srVia == "continue")
                {
                    if (mm == null) throw new InvalidOperationException("MenuManager.main is null");
                    mm.LoadGameAndContinueSave();
                    return;
                }
                if (srVia == "confirm")
                {
                    if (sel == null) throw new InvalidOperationException("character selector not found");
                    sel.StartGame();
                    return;
                }

                // 直接触发「去冒险吧」按钮自己的 onClick —— 和真点一下等价，
                // 不会漏掉按钮上挂的前置校验。按钮是 nonpublic，只能反射拿。
                if (srVia == "button")
                {
                    if (sel == null) throw new InvalidOperationException("character selector not found");
                    UnityEngine.UI.Button btn =
                        World.Field(sel, "goAdventuringButton") as UnityEngine.UI.Button;
                    if (btn == null) throw new InvalidOperationException("goAdventuringButton not found");
                    btn.onClick.Invoke();
                    return;
                }

                // 选角色（StartGame 之前必须先选，实测不选的话点了没反应）
                if (srVia == "choose")
                {
                    if (sel == null) throw new InvalidOperationException("character selector not found");
                    string want = MiniJson.Str(pc.Action, "character", "");
                    object raw = World.Field(sel, "characterList");
                    System.Collections.IList list = raw as System.Collections.IList;
                    if (list == null || list.Count == 0)
                        throw new InvalidOperationException("characterList empty");

                    Character pick = null;
                    for (int i = 0; i < list.Count; i++)
                    {
                        Character ch = list[i] as Character;
                        if (ch == null) continue;
                        if (pick == null) pick = ch;
                        if (want.Length == 0) continue;
                        string nm = "";
                        try { nm = ch.characterName.ToString(); } catch { }
                        if (string.Equals(nm, want, StringComparison.OrdinalIgnoreCase))
                        {
                            pick = ch;
                            break;
                        }
                    }
                    if (pick == null) throw new InvalidOperationException("no usable Character in list");
                    sel.ChooseCharacter(pick);
                    return;
                }

                if (mm != null) { mm.GoAdventuring(); return; }
                if (sel != null) { sel.StartGame(); return; }
                throw new InvalidOperationException("neither MenuManager nor NewCharacterSelector found");
            }

            // 升级界面开着时用它收尾（EndLevelUp 是 public）。
            // 注意：**只调 EndLevelUp 是不够的**——实测界面关了但 can_level_up 又变回 true，
            // 于是 agent 无限循环「点升级 → 关掉」。还得先把格子选掉：
            //   via=end（默认）  只 EndLevelUp（原样，留作对照）
            //   via=spaces        ChangeSpaces(numberToUnlock) 再 EndLevelUp
            //   via=select        Select() × numberToUnlock 再 EndLevelUp
            if (type == "finish_level_up")
            {
                LevelUpManager lumEnd = LevelUpManager.main;
                if (lumEnd == null) throw new InvalidOperationException("LevelUpManager.main is null");

                string lvia = MiniJson.Str(pc.Action, "via", "end");
                int need = 1;
                try { need = lumEnd.numberToUnlock; } catch { }
                if (need <= 0) need = 1;

                if (lvia == "spaces")
                {
                    int n = (int)MiniJson.Num(pc.Action, "spaces", need);
                    if (n <= 0) n = need;
                    lumEnd.ChangeSpaces(n);
                }
                else if (lvia == "select")
                {
                    int times = (int)MiniJson.Num(pc.Action, "times", need);
                    if (times <= 0) times = need;
                    for (int i = 0; i < times; i++) lumEnd.Select();
                }

                lumEnd.EndLevelUp();
                return;
            }

            // 把地上的战利品放进背包。掉落物在战斗结束瞬间会被销毁（SPIKE-06 §18），
            // 所以这是「不丢东西」的唯一办法。
            //
            // 摆放没有单一公开入口：StartDrag/LookForSpace/StopDrag 是 public，
            // 但 EndDrag 和 GameManager.MoveItemTowardsGrid 是 private，只能反射。
            // 所以这里用 via 留了五条路，一次部署就能全试。
            if (type == "place_item")
            {
                string placeUid = MiniJson.Str(pc.Action, "uid", "");
                Item2 placeItem = FindItem(placeUid);
                if (placeItem == null) throw new InvalidOperationException("item not found: " + placeUid);

                ItemMovement im = null;
                try { im = placeItem.GetItemMovement(); } catch { }
                if (im == null) throw new InvalidOperationException("no ItemMovement on " + placeUid);

                // 目标位置：优先按物品形状找能放下的空格（P1）。
                // 旧行为是「第一个空格」，对 2x2 / 异形物品经常放不下；
                // 现在用物品自己的 collider 形状算出「放到这个格子会占哪些格」，
                // 再检查这些格子是否都空着。skip 用于失败后换下一个候选。
                string placeVia = MiniJson.Str(pc.Action, "via", "fit");
                int skip = (int)MiniJson.Num(pc.Action, "skip", 0);
                if (skip < 0) skip = 0;

                Vector3 target = im.transform.position;
                bool hasTarget = false;
                int freeSquares = 0;
                int fitCount = 0;
                int spaceCount = -1;

                List<GridSquare> openSquares = new List<GridSquare>();
                try
                {
                    List<GridSquare> all = GridSquare.allGrids;
                    if (all != null)
                    {
                        for (int i = 0; i < all.Count; i++)
                        {
                            GridSquare gs = all[i];
                            if (!SquareOpen(gs)) continue;
                            freeSquares++;
                            openSquares.Add(gs);
                        }
                    }
                }
                catch { }

                // GetGridPositionsAtPosition / WorldToCell 都是只读：只算形状，不移动物品。
                GridObject placeGrid = null;
                try { placeGrid = placeItem.gridObject; } catch { }
                List<Vector3> fitTargets = new List<Vector3>();
                if (placeGrid != null && placeVia != "drag")
                {
                    Dictionary<string, GridSquare> openByCell = new Dictionary<string, GridSquare>();
                    for (int i = 0; i < openSquares.Count; i++)
                    {
                        GridSquare gs = openSquares[i];
                        try
                        {
                            object cellObj = World.CallPrivate(placeGrid, "WorldToCell",
                                new object[] {
                                    new Vector2(gs.transform.position.x, gs.transform.position.y)
                                });
                            if (cellObj == null) continue;
                            Vector2Int cell = (Vector2Int)cellObj;
                            openByCell[cell.x + "," + cell.y] = gs;
                        }
                        catch { }
                    }

                    for (int i = 0; i < openSquares.Count; i++)
                    {
                        GridSquare gs = openSquares[i];
                        List<Vector2Int> cells = null;
                        try
                        {
                            cells = World.CallPrivate(placeGrid, "GetGridPositionsAtPosition",
                                new object[] {
                                    new Vector2(gs.transform.position.x, gs.transform.position.y)
                                }) as List<Vector2Int>;
                        }
                        catch { }
                        if (cells == null || cells.Count == 0) continue;
                        bool fits = true;
                        for (int c = 0; c < cells.Count; c++)
                        {
                            string key = cells[c].x + "," + cells[c].y;
                            if (!openByCell.ContainsKey(key)) { fits = false; break; }
                        }
                        if (fits) fitTargets.Add(gs.transform.position);
                    }
                }
                fitCount = fitTargets.Count;
                // 连续放多件时轮转，避免都塞进第一个空格（实测 4 件全叠在 10,2）。
                int pick = skip > 0 ? skip : _placeCursor++;

                if (fitCount > 0)
                {
                    target = fitTargets[pick % fitCount];
                    hasTarget = true;
                }
                else if (openSquares.Count > 0)
                {
                    // 形状算不出来（老物品/异常）：按游标轮转空格盲试。
                    target = openSquares[pick % openSquares.Count].transform.position;
                    hasTarget = true;
                }
                else
                {
                    try
                    {
                        List<GameObject> spaces = placeItem.FindGridSpaces();
                        if (spaces != null)
                        {
                            spaceCount = spaces.Count;
                            for (int i = 0; i < spaces.Count; i++)
                            {
                                if (spaces[i] == null) continue;
                                target = spaces[i].transform.position;
                                hasTarget = true;
                                break;
                            }
                        }
                    }
                    catch { }
                }

                if (!hasTarget)
                    throw new InvalidOperationException(
                        "no fitting cell (freeGridSquares=" + freeSquares +
                        " fitShape=" + fitCount + " findGridSpaces=" + spaceCount + ")");

                if (placeVia == "look")
                {
                    im.LookForSpace(new Vector2(target.x, target.y), 0);
                }
                else if (placeVia == "look3")
                {
                    im.LookForSpace(new Vector2(target.x, target.y), 3);
                }
                else if (placeVia == "gm")
                {
                    World.CallPrivate(GameManager.main, "MoveItemTowardsGrid", new object[] { im });
                }
                else if (placeVia == "stopdrag")
                {
                    im.StartDrag();
                    im.transform.position = target;
                    MelonCoroutines.Start(im.StopDrag());
                }
                else if (placeVia == "snap")
                {
                    im.transform.position = target;
                    GridObject placeGo = null;
                    try { placeGo = placeItem.gridObject; } catch { }
                    if (placeGo != null) placeGo.SnapToGrid();
                }
                else if (placeVia == "parent_snap")
                {
                    try
                    {
                        GameManager gmPlace = GameManager.main;
                        if (gmPlace != null) placeItem.parentInventoryGrid = gmPlace.inventoryItemsParent;
                    }
                    catch { }
                    im.transform.position = target;
                    GridObject placeGo2 = null;
                    try { placeGo2 = placeItem.gridObject; } catch { }
                    if (placeGo2 != null) placeGo2.SnapToGrid();
                }
                else
                {
                    // P1：先把物品挂到持久背包的 parent 上，再走正常拖拽。
                    // via=bag：挂在 GridSquare 自己的 parent 下（主背包），
                    // 而不是 GameManager.inventoryItemsParent（那是包外/掉落区，
                    // 实测会把物品放到 4,2 / 10,2，背包画面仍是空的）。
                    Transform placeParent = null;
                    if (placeVia == "bag")
                    {
                        try
                        {
                            List<GridSquare> gsAll = GridSquare.allGrids;
                            if (gsAll != null && gsAll.Count > 0 && gsAll[0] != null)
                                placeParent = gsAll[0].transform.parent;
                        }
                        catch { }
                    }
                    if (placeParent == null)
                    {
                        try
                        {
                            GameManager gmPlace = GameManager.main;
                            if (gmPlace != null) placeParent = gmPlace.inventoryItemsParent;
                        }
                        catch { }
                    }
                    if (placeParent != null) placeItem.parentInventoryGrid = placeParent;
                    im.StartDrag();
                    im.transform.position = target;
                    World.CallPrivate(im, "EndDrag", null);
                }
                return;
            }

            if (type == "close_popup")
            {
                CloseAllPopups();
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

        /// <summary>找门：uid 为空时取场上第一个（当前场景通常只有一个）。</summary>
        // ---------- 宝箱 / 事件诊断（P0） ----------

        /// <summary>当前房间：优先 DungeonPlayer.room，退回到最近房间（&lt;0.6）。</summary>
        private static DungeonRoom CurrentRoom()
        {
            DungeonPlayer dp = DungeonPlayer.main;
            if (dp == null) return null;
            DungeonRoom r = World.Field(dp, "room") as DungeonRoom;
            if (r != null) return r;
            float dist = 0f;
            return World.NearestRoom(dp.transform.position, World.RoomSnapDistance, out dist);
        }

        private static List<Chest> ClosedChestsInRoom(DungeonRoom room)
        {
            List<Chest> outList = new List<Chest>();
            if (room == null) return outList;
            try
            {
                Chest[] chests = room.GetComponentsInChildren<Chest>(true);
                if (chests == null) return outList;
                for (int i = 0; i < chests.Length; i++)
                {
                    Chest c = chests[i];
                    if (c == null) continue;
                    object v = World.Field(c, "isOpen");
                    if (v is bool && (bool)v) continue;
                    outList.Add(c);
                }
            }
            catch { }
            return outList;
        }

        /// <summary>
        /// Play() 把宝箱实例挂到 Player.main.transform.parent（不一定是 DungeonRoom），
        /// 所以房间内查找会漏。按 dungeonEvent 绑定或离事件位置 &lt;2 来找。
        /// </summary>
        private static List<Chest> GlobalChests(DungeonEvent ev, Vector3 near)
        {
            List<Chest> outList = new List<Chest>();
            try
            {
                UnityEngine.Object[] objs = UnityEngine.Object.FindObjectsOfType(typeof(Chest));
                if (objs == null) return outList;
                for (int i = 0; i < objs.Length; i++)
                {
                    Chest c = objs[i] as Chest;
                    if (c == null) continue;
                    object open = World.Field(c, "isOpen");
                    if (open is bool && (bool)open) continue;
                    bool linked = ev != null && World.Field(c, "dungeonEvent") == ev;
                    float dist = Vector2.Distance(
                        new Vector2(c.transform.position.x, c.transform.position.y),
                        new Vector2(near.x, near.y));
                    if (linked || dist < 2f) outList.Add(c);
                }
            }
            catch { }
            return outList;
        }

        private static List<DungeonEvent> EventsInRoom(DungeonRoom room)
        {
            List<DungeonEvent> outList = new List<DungeonEvent>();
            if (room == null) return outList;
            try
            {
                DungeonEvent[] evs = room.GetComponentsInChildren<DungeonEvent>(true);
                if (evs != null) outList.AddRange(evs);
            }
            catch { }
            return outList;
        }

        private static int ObjId(UnityEngine.Object o)
        {
            try { return o == null ? 0 : o.GetInstanceID(); } catch { return 0; }
        }

        private static int EventType(DungeonEvent ev)
        {
            try { object v = World.Field(ev, "dungeonEventType"); return v == null ? -1 : Convert.ToInt32(v); }
            catch { return -1; }
        }

        private static bool EventFinished(DungeonEvent ev)
        {
            try { object v = World.CallPrivate(ev, "IsFinished", null); return v is bool && (bool)v; }
            catch { return true; }
        }

        private static int EventItemsToSpawn(DungeonEvent ev)
        {
            try
            {
                System.Collections.IList l = World.Field(ev, "itemsToSpawn") as System.Collections.IList;
                return l == null ? -1 : l.Count;
            }
            catch { return -1; }
        }

        private static bool EventOpenProp(DungeonEvent ev)
        {
            try { return World.CallPrivateEnum(ev, "GetEventProperty", 6) != null; }
            catch { return false; }
        }

        private static int EventMimic(DungeonEvent ev)
        {
            try
            {
                object v = World.CallPrivateEnum(ev, "GetEventPropertyValue", 3);
                return v == null ? 0 : Convert.ToInt32(v);
            }
            catch { return 0; }
        }

        private static bool ChestBound(Chest c)
        {
            return c != null && World.Field(c, "dungeonEvent") != null;
        }

        private static void WriteEventDiag(JsonWriter w, DungeonEvent ev)
        {
            w.BeginObject();
            w.Prop("uid", "d" + ObjId(ev));
            w.Prop("type", EventType(ev));
            w.Prop("finished", EventFinished(ev));
            w.Prop("items_to_spawn", EventItemsToSpawn(ev));
            w.Prop("open_prop", EventOpenProp(ev));
            w.Prop("mimic_value", EventMimic(ev));
            w.EndObject();
        }

        private static void WriteChestDiag(JsonWriter w, Chest c)
        {
            w.BeginObject();
            w.Prop("name", c == null ? "" : c.name);
            w.Prop("uid", "c" + ObjId(c));
            object open = c == null ? null : World.Field(c, "isOpen");
            w.Prop("is_open", open is bool && (bool)open);
            object type = c == null ? null : World.Field(c, "type");
            w.Prop("type", type == null ? -1 : Convert.ToInt32(type));
            object num = c == null ? null : World.Field(c, "numOfItems");
            w.Prop("num_of_items", num == null ? -1 : Convert.ToInt32(num));
            w.Prop("dungeon_event_bound", ChestBound(c));
            object starting = c == null ? null : World.Field(c, "startingItems");
            System.Array arr = starting as System.Array;
            w.Prop("starting_items", arr == null ? -1 : arr.Length);
            if (c != null)
            {
                w.Prop("x", c.transform.position.x);
                w.Prop("y", c.transform.position.y);
            }
            w.EndObject();
        }

        private static void DiagEventAction(PendingCommand pc)
        {
            JsonWriter w = new JsonWriter();
            w.BeginObject();

            DungeonPlayer dp = DungeonPlayer.main;
            DungeonRoom room = CurrentRoom();
            w.Name("player").BeginObject();
            if (dp != null)
            {
                w.Prop("x", dp.transform.position.x);
                w.Prop("y", dp.transform.position.y);
            }
            w.Prop("room", room == null ? "" : World.RoomUid(room));
            w.EndObject();

            DungeonEvent rel = null;
            if (dp != null)
            {
                try
                {
                    rel = World.CallPrivate(dp, "GetRelevantEvent",
                        new object[] { new Vector2(dp.transform.position.x, dp.transform.position.y) })
                        as DungeonEvent;
                }
                catch { }
            }
            w.Name("relevant_event");
            if (rel == null) w.Null(); else WriteEventDiag(w, rel);

            List<DungeonEvent> evs = EventsInRoom(room);
            w.Name("room_events").BeginArray();
            for (int i = 0; i < evs.Count; i++) WriteEventDiag(w, evs[i]);
            w.EndArray();

            List<Chest> chests = ClosedChestsInRoom(room);
            w.Name("closed_chests").BeginArray();
            for (int i = 0; i < chests.Count; i++) WriteChestDiag(w, chests[i]);
            w.EndArray();

            // 全局 Chest（Play 可能把实例挂到 Player.main.transform.parent 下）
            w.Name("global_chests").BeginArray();
            try
            {
                UnityEngine.Object[] allChests =
                    UnityEngine.Object.FindObjectsOfType(typeof(Chest));
                for (int i = 0; allChests != null && i < allChests.Length && i < 16; i++)
                {
                    Chest c = allChests[i] as Chest;
                    if (c != null) WriteChestDiag(w, c);
                }
            }
            catch { }
            w.EndArray();

            int gridTotal = -1;
            int free = -1;
            try
            {
                List<GridSquare> grids = GridSquare.allGrids;
                if (grids != null)
                {
                    gridTotal = grids.Count;
                    free = 0;
                    w.Name("grid").BeginArray();
                    for (int i = 0; i < grids.Count; i++)
                    {
                        GridSquare gs = grids[i];
                        if (gs == null) continue;
                        if (SquareOpen(gs)) free++;
                        w.BeginObject();
                        w.Prop("x", gs.transform.localPosition.x);
                        w.Prop("y", gs.transform.localPosition.y);
                        w.Prop("parent", gs.transform.parent == null ? "" : gs.transform.parent.name);
                        w.Prop("pouch", World.Field(gs, "isPouch") is bool && (bool)World.Field(gs, "isPouch"));
                        w.Prop("contains", World.Field(gs, "containsItem") is bool && (bool)World.Field(gs, "containsItem"));
                        object t = World.CallPrivate(gs, "TestForItem", null);
                        w.Prop("test_for_item", t is bool && (bool)t);
                        GridObject go = World.Field(gs, "gridObject") as GridObject;
                        w.Prop("obj", go == null ? "" : go.name);
                        w.EndObject();
                    }
                    w.EndArray();
                }
            }
            catch { }
            w.Prop("grid_squares", gridTotal);
            w.Prop("free_squares", free);
            w.EndObject();
            pc.DiagJson = w.ToString();
        }

        private static void OpenChestAction(PendingCommand pc)
        {
            DungeonRoom room = CurrentRoom();
            if (room == null) throw new InvalidOperationException("no current room");

            DungeonEvent chestEvent = null;
            List<DungeonEvent> evs = EventsInRoom(room);
            for (int i = 0; i < evs.Count; i++)
            {
                if (EventType(evs[i]) != 2) continue;
                if (EventFinished(evs[i])) continue;
                chestEvent = evs[i];
                break;
            }

            List<Chest> chests = new List<Chest>();
            if (chestEvent != null)
                chests = GlobalChests(chestEvent, chestEvent.transform.position);
            if (chests.Count == 0) chests = ClosedChestsInRoom(room);

            bool spawnedByPlay = false;
            bool openBefore = false;
            bool openAfterRemove = false;
            if (chests.Count == 0 && chestEvent != null)
            {
                // StartEvent 在真机上连 GetRelevantEvent 都返回 null（见 diag），
                // 所以宝箱对象根本没被 Play() 实例化。这里直接：
                //   1) 去掉事件上的 `open` 属性（真机上它是 0，但 Play 只看存在性）
                //   2) 调 DungeonEvent.Play()，它会按 itemsToSpawn 生成 Chest 对象并绑定 dungeonEvent
                openBefore = EventOpenProp(chestEvent);
                World.CallPrivateEnum(chestEvent, "RemoveEventProperty", 6);
                openAfterRemove = EventOpenProp(chestEvent);
                World.CallPrivate(chestEvent, "Play", null);
                spawnedByPlay = true;
                // Play 把实例挂在 Player.main.transform.parent，不一定是 room 的子节点。
                chests = GlobalChests(chestEvent, chestEvent.transform.position);
                if (chests.Count == 0) chests = ClosedChestsInRoom(room);
            }

            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("room", World.RoomUid(room));
            w.Prop("spawned_by_play", spawnedByPlay);
            w.Prop("open_before", openBefore);
            w.Prop("open_after_remove", openAfterRemove);
            w.Prop("chests_after", chests.Count);
            w.Name("chest_event");
            if (chestEvent == null) w.Null(); else WriteEventDiag(w, chestEvent);

            if (chests.Count == 0)
            {
                w.Prop("error", "chest did not spawn after Play");
                w.EndObject();
                pc.DiagJson = w.ToString();
                throw new InvalidOperationException("chest did not spawn after Play");
            }

            Chest chest = chests[0];
            DungeonEvent bound = World.Field(chest, "dungeonEvent") as DungeonEvent;
            w.Prop("bound_before", bound != null);
            w.Name("chest");
            WriteChestDiag(w, chest);

            if (bound == null && chestEvent != null)
            {
                // Play 正常会自己绑；这里兜底一次，OpenChest 才会 FinishEvent / 判 mimic。
                World.SetField(chest, "dungeonEvent", chestEvent);
            }
            // Play() 刚实例化的 Chest 还没跑 Start()，gameManager 字段是 null；
            // OpenChest() 第一段就读 gameManager.floor，会 NRE。这里先补上。
            World.SetField(chest, "gameManager", GameManager.main);
            w.Name("bound_after").BeginObject();
            w.Prop("bound", ChestBound(chest));
            w.Prop("game_manager_set", World.Field(chest, "gameManager") != null);
            w.EndObject();
            w.EndObject();

            pc.DiagJson = w.ToString();
            chest.OpenChest();
        }

        /// <summary>MapProbe 用：当前房间能不能开宝箱（已有 Chest，或事件还没 Play）。</summary>
        public static bool HasClosedChestInRoom()
        {
            DungeonRoom room = CurrentRoom();
            if (ClosedChestsInRoom(room).Count > 0) return true;
            List<DungeonEvent> evs = EventsInRoom(room);
            for (int i = 0; i < evs.Count; i++)
            {
                if (EventType(evs[i]) != 2) continue;
                if (EventFinished(evs[i])) continue;
                if (GlobalChests(evs[i], evs[i].transform.position).Count > 0) return true;
                if (EventItemsToSpawn(evs[i]) > 0) return true;
            }
            return false;
        }

        private static Door FindDoor(string uid)
        {
            try
            {
                UnityEngine.Object[] objs =
                    UnityEngine.Object.FindObjectsOfType(typeof(Door));
                if (objs == null || objs.Length == 0) return null;

                for (int i = 0; i < objs.Length; i++)
                {
                    Door d = objs[i] as Door;
                    if (d == null) continue;
                    if (uid == null || uid.Length == 0) return d;
                    if (("o" + d.GetInstanceID()) == uid) return d;
                }
            }
            catch { }
            return null;
        }

        /// <summary>按 "r123" 找到房间对象；找不到返回 null。</summary>
        private static DungeonRoom FindRoom(string uid)
        {
            if (string.IsNullOrEmpty(uid) || uid.Length < 2) return null;
            if (uid[0] != 'r') return null;

            int want;
            if (!int.TryParse(uid.Substring(1), out want)) return null;

            try
            {
                List<DungeonRoom> rooms = DungeonRoom.allRooms;
                if (rooms == null) return null;
                for (int i = 0; i < rooms.Count; i++)
                {
                    DungeonRoom r = rooms[i];
                    if (r == null) continue;
                    if (r.GetInstanceID() == want) return r;
                }
            }
            catch { }
            return null;
        }

        /// <summary>关掉所有 BasicPopUp（教程/提示弹窗）。返回关掉的个数。</summary>
        private static int CloseAllPopups()
        {
            int closed = 0;
            try
            {
                UnityEngine.Object[] objs =
                    UnityEngine.Object.FindObjectsOfType(typeof(BasicPopUp));
                if (objs == null) return 0;
                for (int i = 0; i < objs.Length; i++)
                {
                    BasicPopUp p = objs[i] as BasicPopUp;
                    if (p == null) continue;
                    p.Close();
                    closed++;
                }
            }
            catch { }
            return closed;
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

            // SPIKE-07 §4 第二步：结构化战斗回执。
            // 光有 delta 只能说明「数值变了」，说不清「谁打谁、挡了多少、上了什么状态」；
            // 效果不可见的物品（HEAT POTION / LUCKY STAR / REVERSAL AMULET）就永远学不到。
            w.Name("receipt");
            Receipt.Write(w, pc, after);

            // 只读诊断（diag_event / open_chest）的原始数据。
            w.Name("diag");
            if (pc.DiagJson == null) w.Null();
            else w.Raw(pc.DiagJson);

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
