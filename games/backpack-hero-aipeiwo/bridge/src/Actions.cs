// 动作层：cmd/*.json -> 执行 -> ack/*.json
//
// 这一层只做三件事，和 MVP §3.1 的约定一致：
//   1. 读命令（runtime/cmd/{id}.json
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
        // 没有这几项，任何地图动作都会因为「Snapshot 里没有它」被判成没变
        // (changed=false -> ok=false -> run.py 判卡死)。见 SPIKE-06 §7。
        public string RoomId = "";
        public int ViewingTop = -1;
        public int InventoryPhase = -1;
        public int Gold = -1;
        public int Level = -1;
        public int ItemsTotal = -1;
        public int ItemsPlaced = -1;
        public int ItemsInGrid = -1;
        /// <summary>主背包格子数。扩容（unlock_spaces）只改这个，不改 items_*
        /// 不加它的话扩容动作会被判成 changed=false。
        public int GridSquares = -1;

        /// <summary>背包布局签名（uid→cells）。move/rotate 只改这个，靠它判 changed。</summary>
        public string BagSig = "";
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
            s.GridSquares = Actions.CountGridSquares();
            // 布局签名：move_item / rotate_item 只改 cells，不改物品数
            // 没有它这两个动作会被判成 changed=false
            s.BagSig = Actions.BagSignature();

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
        /// 早先版本 DiffJson 无条件写 "enemies":{}，导致每个动作都被判成有变化
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
            if (a.GridSquares != GridSquares) return true;
            if (a.BagSig != BagSig) return true;
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
            if (after.GridSquares != GridSquares) w.Prop("grid_squares", after.GridSquares - GridSquares);
            if (after.BagSig != BagSig) w.Prop("bag_layout_changed", true);
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

        /// <summary>
        /// place_item 的真验收：这件物品的每个 cell 都在主背包格子里，且 parent 是 Grid Parent。
        /// 不能只看 items_in_grid——AddToGrid 在判定格子够不够之前就置 inGrid=true 了。
        /// </summary>
        public bool WaitInBag;
        public string PlaceUid = "";

        /// <summary>drop_item：等物品真的离开背包（ItemInBag 为 false，或已经被销毁）。</summary>
        public bool WaitDropped;

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

        /// <summary>
        /// 主背包的 parent。实测手动拖入后 `parentInventoryGrid` 是 **Grid Parent**，
        /// 不是 `GameManager.inventoryItemsParent`（那是包外/掉落区，cells 会落在 4,2 / 10,2）。
        /// </summary>
        public static Transform BagParent()
        {
            try
            {
                List<GridSquare> all = GridSquare.allGrids;
                if (all != null && all.Count > 0 && all[0] != null)
                    return all[0].transform.parent;
            }
            catch { }
            return null;
        }

        /// <summary>主背包某个格子的名字（用于验收 parent）。</summary>
        public static string BagParentName()
        {
            Transform t = BagParent();
            return t == null ? "" : t.name;
        }

        /// <summary>主背包的格子坐标集合（"x,y"）。用世界坐标算 cell，和 item.gridPositions 同一套。</summary>
        public static HashSet<string> BagCellSet()
        {
            HashSet<string> set = new HashSet<string>();
            try
            {
                List<GridSquare> all = GridSquare.allGrids;
                if (all == null) return set;
                for (int i = 0; i < all.Count; i++)
                {
                    GridSquare gs = all[i];
                    if (gs == null) continue;
                    Vector2Int c = CellOfSquare(gs);
                    set.Add(c.x + "," + c.y);
                }
            }
            catch { }
            return set;
        }

        /// <summary>格子的 cell 坐标：优先走 GridObject.WorldToCell，和物品的 gridPositions 对齐。</summary>
        public static Vector2Int CellOfSquare(GridSquare gs)
        {
            if (gs == null) return new Vector2Int(0, 0);
            try
            {
                // GridSquare.gridObject 是 nonpublic（种类 dump 里能看到），只能反射。
                GridObject go = World.Field(gs, "gridObject") as GridObject;
                if (go != null)
                {
                    Vector3 p = gs.transform.position;
                    return GridObject.WorldToCell(new Vector2(p.x, p.y));
                }
            }
            catch { }
            try
            {
                Vector3 lp = gs.transform.localPosition;
                return new Vector2Int(Mathf.RoundToInt(lp.x), Mathf.RoundToInt(lp.y));
            }
            catch { }
            return new Vector2Int(0, 0);
        }

        /// <summary>找主背包里指定 cell 的格子。</summary>
        public static GridSquare BagSquareAt(Vector2Int cell)
        {
            try
            {
                List<GridSquare> all = GridSquare.allGrids;
                if (all == null) return null;
                for (int i = 0; i < all.Count; i++)
                {
                    GridSquare gs = all[i];
                    if (gs == null) continue;
                    Vector2Int c = CellOfSquare(gs);
                    if (c.x == cell.x && c.y == cell.y) return gs;
                }
            }
            catch { }
            return null;
        }

        /// <summary>把 "x,y" 解析成 Vector2Int（C#5：不能 out var）。</summary>
        public static bool TryParseCell(string text, out Vector2Int cell)
        {
            cell = new Vector2Int(0, 0);
            if (text == null) return false;
            string[] parts = text.Split(',');
            if (parts.Length != 2) return false;
            int x;
            int y;
            if (!int.TryParse(parts[0].Trim(), out x)) return false;
            if (!int.TryParse(parts[1].Trim(), out y)) return false;
            cell = new Vector2Int(x, y);
            return true;
        }

        /// <summary>把世界坐标换算成 cell（和 gridPositions 同一套）；算不出来就按格距取整。</summary>
        public static Vector2Int CellOfPosition(GridObject go, Vector3 pos)
        {
            try { return GridObject.WorldToCell(new Vector2(pos.x, pos.y)); } catch { }
            return new Vector2Int(Mathf.RoundToInt(pos.x), Mathf.RoundToInt(pos.y));
        }

        /// <summary>假如把物品放在这个位置，它会占哪些 cell（只读，不移动物品）。</summary>
        public static List<Vector2Int> CellsAtPosition(GridObject go, Vector3 pos)
        {
            if (go == null) return null;
            try
            {
                return World.CallPrivate(go, "GetGridPositionsAtPosition",
                    new object[] { new Vector2(pos.x, pos.y) }) as List<Vector2Int>;
            }
            catch { return null; }
        }

        /// <summary>
        /// 预测「把物品放到 target 会占哪些格
        ///
        /// **不能**直接 `GetGridPositionsAtPosition(target)`：它内部 offset = itemPos - target
        /// 再用 offset 算 grid 对齐差；物品离 target 远时预测会多算一格
        /// （实测 WOODEN SWORD 预测 4 格、实际落 3 格，于是挑了个出界的格子）。
        /// 这里先把 transform 挪到 target（offset=0，和 EndDrag→SnapToGrid 的条件一致）
        /// 算完再挪回去
        /// </summary>
        public static List<Vector2Int> PredictCells(ItemMovement im, GridObject go, Vector3 target)
        {
            if (go == null || im == null) return null;
            Vector3 saved = im.transform.position;
            try
            {
                im.transform.position = target;
                return World.CallPrivate(go, "GetGridPositionsAtPosition",
                    new object[] { new Vector2(target.x, target.y) }) as List<Vector2Int>;
            }
            catch { return null; }
            finally
            {
                try { im.transform.position = saved; } catch { }
            }
        }

        /// <summary>cells -> "x,y;x,y"，给回执/诊断用。</summary>
        public static string CellsString(List<Vector2Int> cells)
        {
            if (cells == null) return "";
            System.Text.StringBuilder sb = new System.Text.StringBuilder();
            for (int i = 0; i < cells.Count; i++)
            {
                if (sb.Length > 0) sb.Append(";");
                sb.Append(cells[i].x).Append(",").Append(cells[i].y);
            }
            return sb.ToString();
        }

        /// <summary>主背包格子数（扩容前后会变）。</summary>
        public static int CountGridSquares()
        {
            try
            {
                List<GridSquare> all = GridSquare.allGrids;
                return all == null ? -1 : all.Count;
            }
            catch { return -1; }
        }

        /// <summary>
        /// 背包布局签名：`uid:cells` 排序后拼起来
        /// move_item / rotate_item 只改 cells、不改物品数，没有它会被判成 changed=false
        /// </summary>
        public static string BagSignature()
        {
            try
            {
                List<Item2> items = World.ItemsInGrid();
                if (items == null) return "";
                List<string> parts = new List<string>();
                for (int i = 0; i < items.Count; i++)
                {
                    Item2 it = items[i];
                    if (it == null) continue;
                    List<string> cells = ItemCells(it);
                    if (cells.Count == 0) continue;
                    cells.Sort(StringComparer.Ordinal);
                    parts.Add(it.GetInstanceID() + ":" +
                              string.Join(",", cells.ToArray()));
                }
                parts.Sort(StringComparer.Ordinal);
                return string.Join("|", parts.ToArray());
            }
            catch { return ""; }
        }

        /// <summary>
        /// 拿到物品的 GridObject，必要时现场补建。
        ///
        /// `Item2.gridObject` 可能是 null（例如刚 drop 过的物品），这时
        /// `GetGridPositionsAtPosition` 全废 → 形状算不出来 → 会「盲放」到第一个空格，
        /// 结果**叠在别的物品上**（实测 MEAL 压到 WOODEN SWORD 的格子里）。
        /// `ItemMovement.GetGridObject()` 是 public 且会补建，这里统一走它。
        /// </summary>
        public static GridObject ResolveGridObject(Item2 it, ItemMovement im)
        {
            GridObject go = null;
            try { go = it.gridObject; } catch { }
            if (go != null) return go;
            if (im != null)
            {
                try { im.GetGridObject(); } catch { }
                go = World.Field(im, "gridObject") as GridObject;
                if (go != null)
                {
                    try { it.gridObject = go; } catch { }
                }
            }
            return go;
        }

        /// <summary>
        /// 按 `gridPositions` 重算每个格子的 `containsItem`。
        ///
        /// `RemoveFromGrid` 只清 `gridPositions`，格子上的 `containsItem` 常常还是 true；
        /// 反过来，物品刚挪到某格时，旧格子的 flag 又可能被误清。两种都会让
        /// `free_squares` 报错（13 格那局少报 1）。这里以「所有 inGrid 且有 cells 的物品」
        /// 为唯一事实重算一遍，place/move/rotate/drop 之后都调。
        /// </summary>
        public static void RefreshOccupancy()
        {
            try
            {
                List<GridSquare> all = GridSquare.allGrids;
                if (all == null) return;

                HashSet<string> used = new HashSet<string>();
                List<Item2> items = World.ItemsInGrid();
                if (items != null)
                {
                    for (int i = 0; i < items.Count; i++)
                    {
                        Item2 it = items[i];
                        if (it == null) continue;
                        if (!World.IsPlaced(it)) continue;
                        List<string> cells = ItemCells(it);
                        for (int c = 0; c < cells.Count; c++) used.Add(cells[c]);
                    }
                }

                for (int i = 0; i < all.Count; i++)
                {
                    GridSquare gs = all[i];
                    if (gs == null) continue;
                    Vector2Int c = CellOfSquare(gs);
                    gs.containsItem = used.Contains(c.x + "," + c.y);
                }
            }
            catch { }
        }

        /// <summary>
        /// 「已经被占的格子」，**以 gridPositions 为唯一事实**，可以排除一两个物品。
        ///
        /// 为什么要这个而不是 `SquareOpen`：`containsItem` 会滞后。move/swap 的中间态
        /// （A 已经被 StartDrag 摘下来、B 还没落位）用 `SquareOpen` 会把 A 的旧格子
        /// 当成占用，于是「B 的旧格也算空」这件事就没法表达。排除 uid 之后，
        /// 占用集合只由「还在背包里、且有 cells 的物品」决定，和 RefreshOccupancy 一致。
        /// </summary>
        public static HashSet<string> OccupiedCells(string exceptUidA, string exceptUidB)
        {
            HashSet<string> used = new HashSet<string>();
            try
            {
                List<Item2> items = World.ItemsInGrid();
                if (items == null) return used;
                for (int i = 0; i < items.Count; i++)
                {
                    Item2 it = items[i];
                    if (it == null) continue;
                    if (!World.IsPlaced(it)) continue;
                    string uid = "i" + it.GetInstanceID();
                    if (uid == exceptUidA || uid == exceptUidB) continue;
                    List<string> cells = ItemCells(it);
                    for (int c = 0; c < cells.Count; c++) used.Add(cells[c]);
                }
            }
            catch { }
            return used;
        }

        /// <summary>footprint 是否整块落在背包里、且不和 `occupied` 相交。返回 null 表示合法。</summary>
        public static string CheckFootprint(List<Vector2Int> fp, HashSet<string> bag,
                                            HashSet<string> occupied)
        {
            if (fp == null || fp.Count == 0) return "cannot compute footprint";
            if (bag == null || bag.Count == 0) return "bag is empty (no grid squares)";
            for (int i = 0; i < fp.Count; i++)
            {
                string key = fp[i].x + "," + fp[i].y;
                if (!bag.Contains(key)) return "footprint leaves bag at " + key;
                if (occupied != null && occupied.Contains(key)) return "footprint hits occupied " + key;
            }
            return null;
        }

        /// <summary>把物品的朝向设成绝对角度（度），只写 transform，不动 startRotation。</summary>
        public static void RotateItemTo(ItemMovement im, int deg)
        {
            if (im == null) return;
            try { im.transform.eulerAngles = new Vector3(0f, 0f, deg); } catch { }
        }

        /// <summary>物品要占几格（形状面积；旋转不改变它，只影响 footprint 的摆法）。</summary>
        public static int SpacesNeeded(Item2 it, ItemMovement im)
        {
            if (im == null)
            {
                try { im = it == null ? null : it.GetItemMovement(); } catch { }
            }
            if (im == null) return -1;
            try { return im.GetSpacesNeeded(); } catch { return -1; }
        }

        /// <summary>被动饰品（Item2+ItemType.Relic = 22）。丢弃时优先避开它们。</summary>
        public static bool IsRelicItem(Item2 it)
        {
            if (it == null) return false;
            try
            {
                List<Item2.ItemType> types = it.itemType;
                if (types == null) return false;
                for (int i = 0; i < types.Count; i++)
                {
                    if ((int)types[i] == 22) return true;
                }
            }
            catch { }
            return false;
        }

        /// <summary>使用要花多少金币（Item2+Cost+Type: energy=0, mana=1, gold=2）。</summary>
        public static int ItemGoldCost(Item2 it)
        {
            if (it == null) return 0;
            int gold = 0;
            try
            {
                List<Item2.Cost> costs = it.costs;
                if (costs == null) return 0;
                for (int i = 0; i < costs.Count; i++)
                {
                    Item2.Cost c = costs[i];
                    if (c == null) continue;
                    if ((int)c.type == 2) gold += c.currentValue;
                }
            }
            catch { }
            return gold;
        }

        /// <summary>
        /// 按物品当前形状，找出背包里所有放得下的落点（transform 目标坐标）
        /// `extraFree` 里的格子当作空（move_item 挪走自己时，旧格子就是这个状态）
        /// </summary>
        public static List<Vector3> FitTargets(ItemMovement im, GridObject go, HashSet<string> extraFree)
        {
            List<Vector3> outList = new List<Vector3>();
            if (im == null) return outList;
            try
            {
                List<GridSquare> all = GridSquare.allGrids;
                if (all == null) return outList;
                for (int i = 0; i < all.Count; i++)
                {
                    GridSquare gs = all[i];
                    if (gs == null) continue;
                    Vector3 pos = gs.transform.position;
                    List<Vector2Int> cells = PredictCells(im, go, pos);
                    if (cells == null || cells.Count == 0) continue;
                    bool fits = true;
                    for (int c = 0; c < cells.Count; c++)
                    {
                        string key = cells[c].x + "," + cells[c].y;
                        GridSquare fs = BagSquareAt(cells[c]);
                        if (fs == null) { fits = false; break; }   // 出背
                        if (extraFree != null && extraFree.Contains(key)) continue;
                        if (!SquareOpen(fs)) { fits = false; break; }
                    }
                    if (fits) outList.Add(pos);
                }
            }
            catch { }
            return outList;
        }

        /// <summary>背包外的一个落点（丢弃物品用），保证不在任何格子上。</summary>
        public static Vector3 DropPosition()
        {
            float maxX = 0f;
            float minY = 0f;
            bool any = false;
            try
            {
                List<GridSquare> all = GridSquare.allGrids;
                if (all != null)
                {
                    for (int i = 0; i < all.Count; i++)
                    {
                        GridSquare gs = all[i];
                        if (gs == null) continue;
                        Vector3 p = gs.transform.position;
                        if (!any) { maxX = p.x; minY = p.y; any = true; }
                        else
                        {
                            if (p.x > maxX) maxX = p.x;
                            if (p.y < minY) minY = p.y;
                        }
                    }
                }
            }
            catch { }
            if (!any) return new Vector3(30f, -7f, 0f);
            return new Vector3(maxX + 6f, minY - 2f, 0f);
        }

        /// <summary>主背包当前空格数。</summary>
        public static int CountFreeSquares()
        {
            int free = 0;
            try
            {
                List<GridSquare> all = GridSquare.allGrids;
                if (all == null) return -1;
                for (int i = 0; i < all.Count; i++)
                {
                    if (SquareOpen(all[i])) free++;
                }
            }
            catch { return -1; }
            return free;
        }

        /// <summary>物品现在的 cells（"x,y"，按 gridPositions）。</summary>
        public static List<string> ItemCells(Item2 it)
        {
            List<string> cells = new List<string>();
            if (it == null) return cells;
            try
            {
                GridObject go = it.gridObject;
                if (go == null)
                {
                    // 有些物品还没跑过 Start()，Item2.gridObject 是空的；
                    // ItemMovement 上那份（private）通常已经有了。
                    try
                    {
                        ItemMovement im2 = it.GetItemMovement();
                        if (im2 != null) go = World.Field(im2, "gridObject") as GridObject;
                    }
                    catch { }
                }
                if (go == null || go.gridPositions == null) return cells;
                for (int i = 0; i < go.gridPositions.Count; i++)
                {
                    Vector2Int v = go.gridPositions[i];
                    cells.Add(v.x + "," + v.y);
                }
            }
            catch { }
            return cells;
        }

        /// <summary>
        /// 物品是否真的在主背包里：每个 cell 都是主背包的格子，且 parent 是 Grid Parent。
        ///
        /// **不要用 `items_in_grid` 当验收**：`AddToGrid` 在检查格子够不够之前就把
        /// `inGrid=true` 了，放包外（Items 容器 4,2 / 10,2）也会 +1。见 RESULT §2.2。
        /// </summary>
        public static bool ItemInBag(string uid)
        {
            Item2 it = FindItem(uid);
            if (it == null) return false;

            List<string> cells = ItemCells(it);
            if (cells.Count == 0) return false;

            HashSet<string> bag = BagCellSet();
            if (bag.Count == 0) return false;
            for (int i = 0; i < cells.Count; i++)
            {
                if (!bag.Contains(cells[i])) return false;
            }

            Transform p = null;
            try { p = it.parentInventoryGrid; } catch { }
            Transform bagp = BagParent();
            if (bagp == null) return true;
            if (p == null) return false;
            if (p == bagp) return true;
            return p.name == bagp.name;
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
                // 旧写法会干等 15 秒然后报假超时（SPIKE-06 §21）
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
                // 坏命令直接丢掉，别卡住队
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
            // GameManager 上有 finishReorganizingButton，onClick 绑的就是这个方法
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
                // 玩家点房间走的是 DungeonRoom.OnPressStart -> MoveToRoom()
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

            // 只读诊断：角色升级表（每一级的 rewards[0].rewardValue 就是扩容来源）
            if (type == "diag_levelups")
            {
                DiagLevelUpsAction(pc);
                return;
            }

            // 只读诊断：整层所有事件 + 它们 itemsToSpawn 的 prefab 元数据
            // （哪种 SimpleClickItem、effects 是什么 type）。不用走过去就能知道
            // 哪个事件会掉物品（type=4）、哪个只是 QuickBoost(+HP/经验/金币)。
            if (type == "diag_events")
            {
                DiagEventsAction(pc);
                return;
            }

            // 只读诊断：所有 Item2（活跃 / 未激活分开列）。
            // `items_loose` 只统计 allItems 里的，未激活的物品在 state 上等于不存在——
            // 用它区分「生成了但没激活」和「根本没生成」。
            if (type == "diag_items")
            {
                DiagItemsAction(pc);
                return;
            }

            // 只读诊断：摆放的形状/落点。逐格打印「如果放这里会占哪些格」，
            // 用来解释 place_item 为什么挑了那个 target（旋转、collider offset 都能看见）。
            if (type == "diag_place")
            {
                DiagPlaceAction(pc);
                return;
            }

            // 只读几何探针：4 个朝向 × 每个背包格子的 footprint 表。
            // 「按朝向找落点」「swap / 腾位」都靠这张表在 agent 侧做搜索（见 agent/backpack.py）。
            if (type == "probe_fit")
            {
                ProbeFitAction(pc);
                return;
            }

            // 扩容：升级界面的「加格子」入口是点 GridExtension，不是 ChangeSpaces。
            // IL
            //   GridExtension.Click -> levelUpManager.Select() -> ChangeSpaces(-1) + SetupSpaces()
            //   ChangeSpaces(num) 只是 numberToUnlock += num（**累加**），
            //   之前 via=spaces 用 numberToUnlock 当参数反复调用，把它从 1 翻到了 256。
            if (type == "unlock_spaces")
            {
                UnlockSpacesAction(pc);
                return;
            }

            // **测试钩子**：给当前升级界面塞 n 个「可加格子」配额，再跑真实 UI 流程。
            //
            // 为什么需要：IL 里 `<LevelUp>d__39` 对 rewardType=0/2 都是
            // `numberToUnlock = spacesSaved`，而 spacesSaved 只在 EndLevelUp 里被赋值，
            // 新开局恒为 0 —— 于是界面显示「选择 0 个格子」，点任何 GridExtension 都没反应。
            // 这个动作只改配额计数（游戏自己的 ChangeSpaces），加格子仍然走
            // GridExtension.Click 的真实路径，用来把背包从 9 扩到 13 做验收
            // 不要拿它当正常升级流程
            if (type == "grant_spaces")
            {
                GrantSpacesAction(pc);
                return;
            }

            // 清掉「没用完的扩容配额
            //
            // CanLevelUp() = (xp 够 || spacesSaved > 0) && !levelingUp，
            // 而 EndLevelUp 会把 spacesSaved 设成 numberToUnlock —— 于是没花完的配额
            // 会让升级界面无限重开（每次还会 IncrementLevel，等级被刷高）。
            // 扩容验收到 13 格后必须把它清零再收尾。
            if (type == "clear_spaces")
            {
                LevelUpManager lumClear = LevelUpManager.main;
                if (lumClear == null) throw new InvalidOperationException("LevelUpManager.main is null");
                int ntuBefore = lumClear.numberToUnlock;
                int savedBefore = lumClear.spacesSaved;
                lumClear.numberToUnlock = 0;
                lumClear.spacesSaved = 0;
                JsonWriter wc = new JsonWriter();
                wc.BeginObject();
                wc.Prop("number_to_unlock_before", ntuBefore);
                wc.Prop("spaces_saved_before", savedBefore);
                wc.Prop("number_to_unlock_after", lumClear.numberToUnlock);
                wc.Prop("spaces_saved_after", lumClear.spacesSaved);
                wc.EndObject();
                pc.DiagJson = wc.ToString();
                return;
            }

            // 宝箱：StartEvent 对 Chest 是 no-op（真机 + IL 都确认），真正入口是 Chest.OpenChest()。
            // 如果 Chest.dungeonEvent 没绑上，先把当前房间的 Chest DungeonEvent 绑给它。
            if (type == "open_chest")
            {
                OpenChestAction(pc);
                return;
            }

            // 通用事件入口：`DungeonEvent.Play()` +（可选）`SimpleClickItem.Click()`。
            // 地图上的 SimpleClickItem 用 `start_event` 是空操作，只能这样收。
            if (type == "play_event")
            {
                PlayEventAction(pc);
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
                // ConsiderWalk / StartWalk 都是 private；public 的入口只有这三个
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
            // 不用模拟鼠标（实测这个环境下鼠标点到哪儿都不可靠）
            //   via=adventure -> MenuManager.GoAdventuring()   「快速开始
            //   via=story     -> MenuManager.StartGameButton()  「故事模式
            //   via=continue  -> MenuManager.LoadGameAndContinueSave()
            //   via=confirm   -> NewCharacterSelector.StartGame() 「去冒险吧
            //   via=auto      -> 有菜单就点快速开始，否则点去冒险
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
            //   via=spaces       等价于 select：Select() 才是「花掉一个扩容配额」，
            //                    ChangeSpaces(+n) 是**加配额**，以前拿 numberToUnlock 当参数
            //                    反复调用，把它从 1 翻到了 256（v8.7 真机踩到）。
            //   via=select        Select() × numberToUnlock 再 EndLevelUp
            if (type == "finish_level_up")
            {
                LevelUpManager lumEnd = LevelUpManager.main;
                if (lumEnd == null) throw new InvalidOperationException("LevelUpManager.main is null");

                string lvia = MiniJson.Str(pc.Action, "via", "end");
                int need = 1;
                try { need = lumEnd.numberToUnlock; } catch { }
                if (need <= 0) need = 1;

                if (lvia == "spaces" || lvia == "select")
                {
                    int times = (int)MiniJson.Num(pc.Action,
                        lvia == "spaces" ? "spaces" : "times", need);
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

                // StartDrag 会把 euler.z 归整到 90° 的倍数（IL: round(z/90)*90），
                // 但 GetColliderSize 只在 z%180==90 时交换 x/y。如果先算形状再 StartDrag，
                // 预测和实际会差一格——实测 WOODEN SWORD 预测放得下、实际落到 -1,2（出界）。
                // 所以先做同样的归整，后面所有格子计算才和落点一致。
                try
                {
                    float z = Mathf.Round(im.transform.eulerAngles.z / 90f) * 90f;
                    im.transform.eulerAngles = new Vector3(0f, 0f, z);
                }
                catch { }

                // 目标位置：优先按物品形状找能放下的空格（P1）
                // 旧行为是「第一个空格」，对 2x2 / 异形物品经常放不下；
                // 现在用物品自己的 collider 形状算出「放到这个格子会占哪些格」，
                // 再检查这些格子是否都空着。skip 用于失败后换下一个候选。
                string placeVia = MiniJson.Str(pc.Action, "via", "start");
                // 朝向：`rot_deg` 是**绝对**角度（0/90/180/270），由 agent 的 planner 指定。
                // 有了它，「按朝向找落点」就是把同一张 probe_fit 表里某个朝向的 footprint
                // 原样发回来，预测和落点不会再有偏差。不给就沿用当前朝向。
                int rotDeg = (int)MiniJson.Num(pc.Action, "rot_deg", int.MinValue);
                if (rotDeg != int.MinValue) RotateItemTo(im, rotDeg);

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

                // GetGridPositionsAtPosition / WorldToCell 都是只读：只算形状，不移动物品
                GridObject placeGrid = ResolveGridObject(placeItem, im);
                // 没有 GridObject 就算不出形状。以前会退化成「盲放第一个空格」，
                // 结果叠在别的物品上（实测 MEAL 压到 WOODEN SWORD）。宁可直接报错。
                if (placeGrid == null && placeVia != "drag")
                    throw new InvalidOperationException("no GridObject on " + placeUid + " (shape unknown)");
                List<Vector3> fitTargets = new List<Vector3>();

                // 显式落点优先：`cell`（格子坐标，和 state 里的 cells 同一套）+ 上面的 `rot_deg`。
                // 验收教程 3 件 / 13 格时用它，不用赌游标；agent 的 planner 也走这条。
                // 先于「按形状扫空格」处理，否则满包时连指定落点都会被 "no fitting cell" 拦掉。
                string wantCell = MiniJson.Str(pc.Action, "cell", "");
                if (wantCell.Length > 0)
                {
                    Vector2Int wc;
                    if (!TryParseCell(wantCell, out wc))
                        throw new InvalidOperationException("bad cell: " + wantCell);
                    GridSquare wgs = BagSquareAt(wc);
                    if (wgs == null)
                        throw new InvalidOperationException("no bag square at " + wantCell);

                    target = wgs.transform.position;
                    hasTarget = true;

                    // 异形物品：整块 footprint 都要在包里、且都空着
                    List<Vector2Int> want = placeGrid == null
                        ? null : PredictCells(im, placeGrid, target);
                    HashSet<string> occ = OccupiedCells(placeUid, null);
                    string badCell = CheckFootprint(want, BagCellSet(), occ);
                    if (badCell != null)
                        throw new InvalidOperationException("cell " + wantCell + " -> " + badCell);
                }

                if (!hasTarget && placeGrid != null && placeVia != "drag")
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
                            cells = PredictCells(im, placeGrid, gs.transform.position);
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
                // 连续放多件时轮转，避免都塞进第一个空格（实测 4 件全叠在 10,2）
                int pick = skip > 0 ? skip : _placeCursor++;

                // 指定了 cell 就别再猜落点（上面已经验证过 footprint）
                if (!hasTarget && fitCount > 0)
                {
                    target = fitTargets[pick % fitCount];
                    hasTarget = true;
                }
                else if (!hasTarget && openSquares.Count > 0)
                {
                    // 形状算不出来（老物品/异常）：按游标轮转空格盲试。
                    target = openSquares[pick % openSquares.Count].transform.position;
                    hasTarget = true;
                }
                else if (!hasTarget)
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
                    // P1 的落点修复（v8.7）：**EndDrag 会把 transform 拉回 startPosition**
                    // 所以「移到目标格再 EndDrag」无效；真正要改的是 ItemMovement.startPosition。
                    //
                    // IL 证据（ItemMovement.StartDragWithoutChecks / EndDrag）：
                    //   StartDrag 末尾：startRotation = transform.rotation;
                    //                   startPosition = transform.position;
                    //   EndDrag  开头：transform.position = startPosition;
                    //                  transform.rotation = startRotation;
                    //                  AddToGrid(false);   // -> SnapToGrid + 占格判定
                    // 因此顺序必须是：StartDrag -> 移到目标 -> 改 startPosition -> EndDrag。
                    // AddToGrid 自己会把 parentInventoryGrid 设成格子父节点（Grid Parent），
                    // 这里先设一次只是让中途状态也正确。
                    Transform placeParent = BagParent();
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

                    // 连放两件时上一件的 isStoppingDrag 还是 true，StartDrag 会直接 return
                    // （物品没被抬起来，startPosition 还是上一次的）。放置前清掉。
                    World.SetField(im, "isStoppingDrag", false);
                    im.StartDrag();
                    if (!im.isDragging)
                        throw new InvalidOperationException(
                            "StartDrag refused: " + StartDragBlocked(im));

                    im.transform.position = target;
                    Vector3 snapPos = im.transform.position;
                    bool okPos = World.SetField(im, "startPosition", snapPos);
                    // 朝向默认保持「抓住时」的旋转：形状是按它算的，改成 identity
                    // 会让 2x2 / 1x3 的 footprint 和候选格对不上。rot=identity 可强制转正。
                    Quaternion snapRot = im.transform.rotation;
                    if (MiniJson.Str(pc.Action, "rot", "keep") == "identity")
                    {
                        snapRot = Quaternion.identity;
                        im.transform.rotation = snapRot;
                    }
                    bool okRot = World.SetField(im, "startRotation", snapRot);

                    World.CallPrivate(im, "EndDrag", null);
                    // 放完把格子占用按 gridPositions 重算一遍：消耗品留下的旧标记会少报空格。
                    RefreshOccupancy();

                    // 回执里直接写「目标格 / 落点 / parent / 空格」，验收不用去翻 state
                    Vector2Int landed = CellOfPosition(placeGrid, target);
                    JsonWriter dw = new JsonWriter();
                    dw.BeginObject();
                    dw.Prop("via", "start");
                    dw.Prop("uid", placeUid);
                    dw.Prop("target_cell", landed.x + "," + landed.y);
                    dw.Prop("start_position_set", okPos);
                    dw.Prop("start_rotation_set", okRot);
                    dw.Name("cells");
                    List<string> gotCells = ItemCells(placeItem);
                    dw.BeginArray();
                    for (int i = 0; i < gotCells.Count; i++) dw.Value(gotCells[i]);
                    dw.EndArray();
                    dw.Prop("parent", placeItem.parentInventoryGrid == null
                        ? "" : placeItem.parentInventoryGrid.name);
                    dw.Prop("in_bag", ItemInBag(placeUid));
                    dw.Prop("free_squares", CountFreeSquares());
                    // SnapToGrid 对「非 1 倍缩放」会转成协程（异步），放完要等一帧才准；
                    // 把 scale 写进回执，落点异常时先看这一项。
                    Vector3 ls = im.transform.lossyScale;
                    dw.Prop("lossy_scale", ls.x.ToString("0.###") + "," + ls.y.ToString("0.###"));
                    dw.EndObject();
                    pc.DiagJson = dw.ToString();
                }
                return;
            }

            // 背包内移动 / 旋转 / 丢弃（P2.2 / P2.3）。
            // 满包时先靠它们腾位，再放战利品——place_item 只能在真实空格里落子。
            if (type == "move_item" || type == "rotate_item" || type == "drop_item")
            {
                MoveRotateDropAction(pc, type);
                return;
            }

            // 两件互换：一条动作里完成「摘下 A → B 进 A 的旧格 → A 进 B 的旧格」，
            // 中间态（A 的旧格算空）只有在这一条动作内部才存在。见 SwapItemsAction。
            if (type == "swap_items")
            {
                SwapItemsAction(pc);
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
            w.Prop("room", EventRoomUid(ev));
            w.Name("items_to_spawn_detail");
            WriteSpawnPrefabs(w, ev);
            w.EndObject();
        }

        /// <summary>事件所在房间（先沿 parent 找 DungeonRoom，再按坐标找最近的）。</summary>
        private static string EventRoomUid(DungeonEvent ev)
        {
            try
            {
                Transform t = ev.transform.parent;
                for (int guard = 0; t != null && guard < 8; guard++)
                {
                    DungeonRoom r = t.GetComponent<DungeonRoom>();
                    if (r != null) return World.RoomUid(r);
                    if (t.parent == t) break;
                    t = t.parent;
                }
                float dist;
                DungeonRoom near = World.NearestRoom(ev.transform.position, 1.5f, out dist);
                return near == null ? "" : World.RoomUid(near);
            }
            catch { return ""; }
        }

        /// <summary>
        /// 事件 `itemsToSpawn` 里的 **prefab**（不实例化、不 Play）是什么、带不带
        /// SimpleClickItem、那个 SCI 的 `effects` 是什么类型。
        ///
        /// 这是「不用走过去就能知道这个事件点下去会掉东西还是只加属性」的关键：
        /// `effect.type=4(item)` 才会往地上摆物品，QuickBoost 是 0/1/2。
        /// </summary>
        private static void WriteSpawnPrefabs(JsonWriter w, DungeonEvent ev)
        {
            w.BeginArray();
            System.Collections.IList list = null;
            try { list = World.Field(ev, "itemsToSpawn") as System.Collections.IList; } catch { }
            for (int i = 0; list != null && i < list.Count; i++)
            {
                GameObject prefab = list[i] as GameObject;
                w.BeginObject();
                w.Prop("name", prefab == null ? "" : prefab.name);
                SimpleClickItem sci = null;
                int item2Children = 0;
                try
                {
                    if (prefab != null)
                    {
                        sci = prefab.GetComponentInChildren<SimpleClickItem>(true);
                        Item2[] its = prefab.GetComponentsInChildren<Item2>(true);
                        item2Children = its == null ? 0 : its.Length;
                    }
                }
                catch { }
                w.Prop("has_simple_click_item", sci != null);
                w.Prop("item2_children", item2Children);
                System.Collections.IList effects = sci == null ? null
                    : World.Field(sci, "effects") as System.Collections.IList;
                w.Name("effects").BeginArray();
                for (int k = 0; effects != null && k < effects.Count; k++)
                {
                    w.BeginObject();
                    object t = World.Field(effects[k], "type");
                    object n = World.Field(effects[k], "num");
                    w.Prop("type", t == null ? -1 : Convert.ToInt32(t));
                    w.Prop("num", n == null ? 0 : Convert.ToInt32(n));
                    w.EndObject();
                }
                w.EndArray();
                w.EndObject();
            }
            w.EndArray();
        }

        /// <summary>
        /// 只读：**整层**所有 DungeonEvent 一览（含迷雾房间）。只看 prefab 元数据，
        /// 不 Play、不实例化、不改任何东西 —— 用来在「走过去」之前就知道
        /// 哪一格的事件会掉物品（`effects` 里有 `type=4`）。
        /// </summary>
        private static void DiagEventsAction(PendingCommand pc)
        {
            List<DungeonEvent> all = null;
            try { all = DungeonEvent.FindAllDungeonEvents(); } catch { }
            if (all == null) { try { all = DungeonEvent.dungeonEvents; } catch { } }

            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("action", "diag_events");
            w.Prop("floor", EventFloor());
            w.Prop("count", all == null ? -1 : all.Count);
            w.Name("events").BeginArray();
            for (int i = 0; all != null && i < all.Count; i++)
            {
                if (all[i] == null) continue;
                DungeonEvent ev = all[i];
                w.BeginObject();
                w.Prop("uid", "d" + ObjId(ev));
                w.Prop("type", EventType(ev));
                w.Prop("room", EventRoomUid(ev));
                w.Prop("finished", EventFinished(ev));
                w.Prop("open_prop", EventOpenProp(ev));
                w.Prop("items_to_spawn", EventItemsToSpawn(ev));
                w.Name("items_to_spawn_detail");
                WriteSpawnPrefabs(w, ev);
                try
                {
                    w.Prop("x", ev.transform.position.x);
                    w.Prop("y", ev.transform.position.y);
                }
                catch { }
                w.EndObject();
            }
            w.EndArray();
            w.EndObject();
            pc.DiagJson = w.ToString();
        }

        /// <summary>
        /// 只读：这个角色（`Player.chosenCharacter`）的升级表。
        ///
        /// 为什么需要：扩容的真源头是 `Player.IncrementLevel()` 里的
        /// `spacesSaved += levelUps[level].rewards[0].rewardValue`
        /// （交叉引用查出来的，只有这一处加）。所以「升级能不能加格子」完全取决于
        /// 这张表里每一级的 `rewards[0]`：`rewardType=0` 才是加格子的奖励。
        /// </summary>
        private static void DiagLevelUpsAction(PendingCommand pc)
        {
            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("action", "diag_levelups");

            Player pl = null;
            try { pl = Player.main; } catch { }
            LevelUpManager lum = null;
            try { lum = LevelUpManager.main; } catch { }
            if (pl == null) throw new InvalidOperationException("Player.main is null");

            w.Prop("level", pl.level);
            w.Prop("experience", pl.experience);
            if (lum != null)
            {
                w.Prop("spaces_saved", lum.spacesSaved);
                w.Prop("number_to_unlock", lum.numberToUnlock);
                w.Prop("leveling_up", lum.levelingUp);
                w.Prop("can_level_up", lum.CanLevelUp());
            }

            Character ch = null;
            try { ch = pl.chosenCharacter; } catch { }
            w.Prop("character", ch == null ? "" : ch.name);
            System.Collections.IList ups = ch == null ? null
                : World.Field(ch, "levelUps") as System.Collections.IList;
            w.Prop("levelups_count", ups == null ? -1 : ups.Count);
            w.Name("levelups").BeginArray();
            for (int i = 0; ups != null && i < ups.Count && i < 24; i++)
            {
                object lu = ups[i];
                w.BeginObject();
                w.Prop("index", i);
                object xp = World.Field(lu, "xpRequired");
                w.Prop("xp_required", xp == null ? -1 : Convert.ToInt32(xp));
                System.Collections.IList rewards = World.Field(lu, "rewards") as System.Collections.IList;
                w.Name("rewards").BeginArray();
                for (int k = 0; rewards != null && k < rewards.Count; k++)
                {
                    w.BeginObject();
                    object rt = World.Field(rewards[k], "rewardType");
                    object rv = World.Field(rewards[k], "rewardValue");
                    w.Prop("type", rt == null ? -1 : Convert.ToInt32(rt));
                    w.Prop("value", rv == null ? 0 : Convert.ToInt32(rv));
                    w.EndObject();
                }
                w.EndArray();
                w.EndObject();
            }
            w.EndArray();
            w.EndObject();
            pc.DiagJson = w.ToString();
        }

        private static int EventFloor()
        {
            try { return GameManager.main == null ? -1 : GameManager.main.floor; }
            catch { return -1; }
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

        /// <summary>
        /// 摆放诊断（只读）：物品的 transform/collider 形状 + 每个主背包格子的
        /// 「把 transform 放这里会占哪些格」，以及该落点是否在包内且全空
        ///
        /// 用来回答「为什么 place_item 选了 -1,1 却落到 -1,2」这类问题，
        /// 不用靠猜（旋转、collider offset、GetColliderSize 的 x/y 交换都会影响）。
        /// </summary>
        private static void DiagPlaceAction(PendingCommand pc)
        {
            string uid = MiniJson.Str(pc.Action, "uid", "");
            Item2 it = FindItem(uid);
            if (it == null) throw new InvalidOperationException("item not found: " + uid);

            ItemMovement im = null;
            try { im = it.GetItemMovement(); } catch { }
            GridObject go = ResolveGridObject(it, im);

            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("uid", uid);
            w.Prop("name", it.displayName == null ? "" : it.displayName);
            w.Prop("bag_parent", BagParentName());
            w.Prop("free_squares", CountFreeSquares());

            if (im != null)
            {
                Vector3 p = im.transform.position;
                Quaternion q = im.transform.rotation;
                Vector3 s = im.transform.lossyScale;
                w.Name("item").BeginObject();
                w.Prop("x", p.x);
                w.Prop("y", p.y);
                w.Prop("euler_z", q.eulerAngles.z);
                w.Prop("scale", s.x.ToString("0.###") + "," + s.y.ToString("0.###"));
                w.Prop("spaces_needed", im.GetSpacesNeeded());
                w.Prop("in_grid", im.inGrid);
                w.Prop("is_dragging", im.isDragging);
                w.Prop("parent", it.parentInventoryGrid == null ? "" : it.parentInventoryGrid.name);
                w.Name("cells").BeginArray();
                List<string> cells = ItemCells(it);
                for (int i = 0; i < cells.Count; i++) w.Value(cells[i]);
                w.EndArray();
                w.EndObject();

                // collider：本地位置 / 尺寸（GetColliderSize 会按旋转交换 x/y）
                // BoxCollider2D 在 UnityEngine.Physics2DModule 里，build.ps1 不引那个程序集
                // （直接引会 CS1070），所以按 Component + 反射读 size/offset。
                w.Name("colliders").BeginArray();
                try
                {
                    Component[] cols = im.GetComponentsInChildren<Component>();
                    for (int i = 0; cols != null && i < cols.Length && i < 32; i++)
                    {
                        Component c = cols[i];
                        if (c == null) continue;
                        if (c.GetType().Name != "BoxCollider2D") continue;
                        Vector2 sz = Vector2.zero;
                        Vector2 of = Vector2.zero;
                        object so = World.CallPrivate(c, "get_size", null);
                        object oo = World.CallPrivate(c, "get_offset", null);
                        if (so is Vector2) sz = (Vector2)so;
                        if (oo is Vector2) of = (Vector2)oo;
                        w.BeginObject();
                        w.Prop("local_x", c.transform.localPosition.x);
                        w.Prop("local_y", c.transform.localPosition.y);
                        w.Prop("size_x", sz.x);
                        w.Prop("size_y", sz.y);
                        w.Prop("offset_x", of.x);
                        w.Prop("offset_y", of.y);
                        w.EndObject();
                        if (i >= 12) break;
                    }
                }
                catch { }
                w.EndArray();
            }

            // 逐格候选：把 transform 放到这个格子中心，会占哪些 cell、是否可行
            w.Name("candidates").BeginArray();
            HashSet<string> bag = BagCellSet();
            try
            {
                List<GridSquare> all = GridSquare.allGrids;
                for (int i = 0; all != null && i < all.Count; i++)
                {
                    GridSquare gs = all[i];
                    if (gs == null) continue;
                    Vector2Int cell = CellOfSquare(gs);
                    Vector3 pos = gs.transform.position;
                    List<Vector2Int> fp = im != null
                        ? PredictCells(im, go, pos)
                        : CellsAtPosition(go, pos);
                    bool inside = fp != null && fp.Count > 0;
                    if (inside)
                    {
                        for (int c = 0; c < fp.Count; c++)
                        {
                            if (!bag.Contains(fp[c].x + "," + fp[c].y)) { inside = false; break; }
                        }
                    }
                    bool allOpen = inside;
                    if (allOpen)
                    {
                        for (int c = 0; c < fp.Count; c++)
                        {
                            GridSquare fs = BagSquareAt(fp[c]);
                            if (fs == null || !SquareOpen(fs)) { allOpen = false; break; }
                        }
                    }
                    w.BeginObject();
                    w.Prop("cell", cell.x + "," + cell.y);
                    w.Prop("open", SquareOpen(gs));
                    w.Prop("footprint", CellsString(fp));
                    w.Prop("inside_bag", inside);
                    w.Prop("fits", allOpen);
                    w.EndObject();
                }
            }
            catch { }
            w.EndArray();
            w.EndObject();
            pc.DiagJson = w.ToString();
        }

        /// <summary>
        /// 只读几何探针：把一件（或全部）物品在 4 个朝向下、每个背包格子上的 footprint
        /// 一次性吐出来。
        ///
        /// 为什么要这样一个动作：`PredictCells` 只有在游戏里跑得动（要 collider + transform），
        /// 所以「4 朝向 × 所有空格」的搜索放在 C# 里每次都要现场算上百次，放在 agent 侧
        /// 又得一个格子问一次。这里一次把整张表吐出来，agent 拿纯数据做搜索（可以离线单测），
        /// 再把选中的 `{cell, rot_deg}` 发回 `place_item` / `move_item`。
        ///
        /// 只保留「整块 footprint 都在背包里」的落点——包外的永远不可用。
        /// 形状相同的朝向（矩形 collider 的 0°/180°、90°/270°）只留角度最小的那个。
        /// </summary>
        private static void ProbeFitAction(PendingCommand pc)
        {
            string uidWanted = MiniJson.Str(pc.Action, "uid", "");

            HashSet<string> bag = BagCellSet();
            List<GridSquare> squares = null;
            try { squares = GridSquare.allGrids; } catch { }

            List<Item2> items = new List<Item2>();
            if (uidWanted.Length > 0)
            {
                Item2 one = FindItem(uidWanted);
                if (one == null) throw new InvalidOperationException("item not found: " + uidWanted);
                items.Add(one);
            }
            else
            {
                List<Item2> inGrid = World.ItemsInGrid();
                if (inGrid != null)
                {
                    for (int i = 0; i < inGrid.Count && items.Count < 12; i++)
                    {
                        if (inGrid[i] == null || !World.IsPlaced(inGrid[i])) continue;
                        items.Add(inGrid[i]);
                    }
                }
                List<Item2> loose = World.ItemsLoose();
                if (loose != null)
                {
                    for (int i = 0; i < loose.Count && items.Count < 12; i++)
                    {
                        if (loose[i] == null) continue;
                        items.Add(loose[i]);
                    }
                }
            }

            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("bag_parent", BagParentName());
            w.Prop("grid_squares", squares == null ? -1 : squares.Count);
            w.Prop("free_squares", CountFreeSquares());
            w.Name("bag").BeginArray();
            foreach (string cell in bag) w.Value(cell);
            w.EndArray();

            w.Name("items").BeginArray();
            for (int idx = 0; idx < items.Count; idx++)
            {
                Item2 it = items[idx];
                string uid = "i" + ObjId(it);
                ItemMovement im = null;
                try { im = it.GetItemMovement(); } catch { }
                GridObject go = ResolveGridObject(it, im);

                int euler0 = 0;
                if (im != null)
                {
                    try
                    {
                        euler0 = ((int)Mathf.Round(im.transform.eulerAngles.z) % 360 + 360) % 360;
                    }
                    catch { }
                }

                w.BeginObject();
                w.Prop("uid", uid);
                w.Prop("name", it.displayName == null ? "" : it.displayName);
                w.Prop("in_bag", ItemInBag(uid));
                w.Prop("needs", SpacesNeeded(it, im));
                w.Prop("euler", euler0);
                w.Name("cells").BeginArray();
                List<string> mine = ItemCells(it);
                for (int i = 0; i < mine.Count; i++) w.Value(mine[i]);
                w.EndArray();

                w.Name("rotations").BeginArray();
                if (im != null && go != null && squares != null)
                {
                    HashSet<string> seen = new HashSet<string>();
                    for (int r = 0; r < 4; r++)
                    {
                        int deg = r * 90;
                        RotateItemTo(im, deg);

                        List<string> anchors = new List<string>();
                        List<string> footprints = new List<string>();
                        System.Text.StringBuilder sig = new System.Text.StringBuilder();
                        for (int s = 0; s < squares.Count; s++)
                        {
                            GridSquare gs = squares[s];
                            if (gs == null) continue;
                            List<Vector2Int> fp = PredictCells(im, go, gs.transform.position);
                            if (fp == null || fp.Count == 0) continue;

                            bool inside = true;
                            for (int c = 0; c < fp.Count; c++)
                            {
                                if (!bag.Contains(fp[c].x + "," + fp[c].y)) { inside = false; break; }
                            }
                            if (!inside) continue;

                            Vector2Int cell = CellOfSquare(gs);
                            string foot = CellsString(fp);
                            anchors.Add(cell.x + "," + cell.y);
                            footprints.Add(foot);
                            sig.Append(cell.x).Append(',').Append(cell.y)
                               .Append('=').Append(foot).Append(';');
                        }

                        if (!seen.Add(sig.ToString())) continue;   // 和前面某个朝向一模一样

                        w.BeginObject();
                        w.Prop("rot", deg);
                        w.Name("placements").BeginArray();
                        for (int p = 0; p < anchors.Count; p++)
                        {
                            w.BeginObject();
                            w.Prop("cell", anchors[p]);
                            w.Prop("cells", footprints[p]);
                            w.EndObject();
                        }
                        w.EndArray();
                        w.EndObject();
                    }
                }
                w.EndArray();

                // 探针只是临时改 transform 的朝向，算完必须还原（否则下一帧会看到歪的）
                if (im != null) RotateItemTo(im, euler0);
                w.EndObject();
            }
            w.EndArray();
            w.EndObject();
            pc.DiagJson = w.ToString();
        }

        /// <summary>
        /// 测试钩子：`ChangeSpaces(n)` + `SetupSpaces()`，把升级界面的「可加格子」配额设成 n。
        /// 只动游戏自己的计数，加格子仍由 <see cref="UnlockSpacesAction"/> 点真实 GridExtension。
        /// </summary>
        private static void GrantSpacesAction(PendingCommand pc)
        {
            LevelUpManager lum = LevelUpManager.main;
            if (lum == null) throw new InvalidOperationException("LevelUpManager.main is null");

            int n = (int)MiniJson.Num(pc.Action, "n", 1);
            if (n <= 0) n = 1;

            int before = -1;
            try { before = lum.numberToUnlock; } catch { }
            lum.ChangeSpaces(n);
            World.CallPrivate(lum, "SetupSpaces", null);

            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("granted", n);
            w.Prop("number_to_unlock_before", before);
            w.Prop("number_to_unlock_after", lum.numberToUnlock);
            w.Prop("leveling_up", lum.levelingUp);
            w.EndObject();
            pc.DiagJson = w.ToString();
        }

        /// <summary>
        /// 扩容：升级界面里点 n 个 GridExtension，把它们的格子并进主背包。
        ///
        /// 每次 Click 会 `Select()` → `ChangeSpaces(-1)` + `SetupSpaces()`：
        /// 消耗一个 numberToUnlock，重建剩下的候选格。所以点完 n 个再 EndLevelUp。
        /// **不要**用 ChangeSpaces(n) 传正数——那是累加配额，不是加格子。
        /// </summary>
        private static void UnlockSpacesAction(PendingCommand pc)
        {
            LevelUpManager lum = LevelUpManager.main;
            if (lum == null) throw new InvalidOperationException("LevelUpManager.main is null");

            int want = (int)MiniJson.Num(pc.Action, "n", 1);
            if (want <= 0) want = 1;

            int before = -1;
            try { before = lum.numberToUnlock; } catch { }
            int gridBefore = -1;
            try { gridBefore = GridSquare.allGrids == null ? -1 : GridSquare.allGrids.Count; } catch { }

            int clicked = 0;
            // guard：万一 Click 没扣数就停，别死循环。
            for (int guard = 0; guard < want + 8 && clicked < want; guard++)
            {
                int left = 0;
                try { left = lum.numberToUnlock; } catch { break; }
                if (left <= 0) break;

                GridExtension pick = null;
                try
                {
                    List<GridExtension> exts = GridExtension.gridExtensions;
                    if (exts != null)
                    {
                        for (int i = 0; i < exts.Count; i++)
                        {
                            GridExtension e = exts[i];
                            if (e == null || e.selected) continue;
                            pick = e;
                            break;
                        }
                    }
                }
                catch { }
                if (pick == null) break;

                World.CallPrivate(pick, "Click", null);
                clicked++;

                int now = 0;
                try { now = lum.numberToUnlock; } catch { break; }
                if (now >= left) break;   // 没消耗配额，说明这次 Click 没生
            }

            int gridAfter = -1;
            try { gridAfter = GridSquare.allGrids == null ? -1 : GridSquare.allGrids.Count; } catch { }

            // 配额花完了就把 spacesSaved 清零：否则 EndLevelUp 会把它存回去，
            // CanLevelUp() 永远为真，升级界面无限重开。
            if (lum.numberToUnlock <= 0) lum.spacesSaved = 0;

            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("wanted", want);
            w.Prop("clicked", clicked);
            w.Prop("number_to_unlock_before", before);
            w.Prop("number_to_unlock_after", lum.numberToUnlock);
            w.Prop("spaces_saved_after", lum.spacesSaved);
            w.Prop("grid_squares_before", gridBefore);
            w.Prop("grid_squares_after", gridAfter);
            w.Prop("leveling_up", lum.levelingUp);
            w.EndObject();
            pc.DiagJson = w.ToString();
        }

        /// <summary>
        /// 背包内移动 / 旋转 / 丢弃。
        ///
        /// 三个动作都先 `StartDrag()`——IL：
        ///   `ItemMovement.Rotate()` 在 `inGrid == true` 时第一行就 return；
        ///   `RemoveFromGrid()` 才会把 `gridPositions` 清空、`parentInventoryGrid = null`、
        ///   `inGrid/inPouch = false`，也就是把物品从背包逻辑上摘下来
        /// 然后和 place_item 一样：移动 transform → 改 startPosition/startRotation → EndDrag。
        ///
        /// - `move_item {uid, cell?}`：挪到指定格（或按形状找第一个放得下的空格）
        /// - `rotate_item {uid, cell?, times=1}`：转 90°×times 再放
        ///   转完放不下就回原位并报错，不会把物品丢在半空
        /// - `drop_item {uid}`：放到背包外并纠正 `inGrid`，物品变成 loose
        ///   （离开房间时会被游戏自己的 `DestroyAllItemsOutsideGrid` 清掉）。
        /// </summary>
        private static void MoveRotateDropAction(PendingCommand pc, string type)
        {
            string uid = MiniJson.Str(pc.Action, "uid", "");
            Item2 it = FindItem(uid);
            if (it == null) throw new InvalidOperationException("item not found: " + uid);

            ItemMovement im = null;
            try { im = it.GetItemMovement(); } catch { }
            if (im == null) throw new InvalidOperationException("no ItemMovement on " + uid);

            List<string> oldCells = ItemCells(it);
            if (oldCells.Count == 0)
                throw new InvalidOperationException("item is not in the bag: " + uid);

            // 和 place_item 一样先归整朝向（StartDrag 也会这么做），
            // 以免「预测的形状」和「落点的形状」差一格。
            try
            {
                float z0 = Mathf.Round(im.transform.eulerAngles.z / 90f) * 90f;
                im.transform.eulerAngles = new Vector3(0f, 0f, z0);
            }
            catch { }

            if (type == "drop_item")
            {
                DropItemAction(pc, it, im, oldCells);
                return;
            }

            World.SetField(im, "isStoppingDrag", false);
            im.StartDrag();
            if (!im.isDragging)
                throw new InvalidOperationException(
                    "StartDrag refused: " + StartDragBlocked(im));

            Quaternion beforeRot = im.transform.rotation;
            Vector3 beforePos = im.transform.position;

            int times = (int)MiniJson.Num(pc.Action, "times", 1);
            if (times <= 0) times = 1;
            // 朝向：`rot_deg` 是绝对角度（planner 用）；`times` 是相对 90°×n（手搓用）。
            int rotDeg = (int)MiniJson.Num(pc.Action, "rot_deg", int.MinValue);
            if (rotDeg != int.MinValue)
            {
                RotateItemTo(im, rotDeg);
            }
            else if (type == "rotate_item")
            {
                im.transform.eulerAngles =
                    new Vector3(0f, 0f, beforeRot.eulerAngles.z + 90f * times);
            }

            GridObject go = ResolveGridObject(it, im);
            if (go == null)
                throw new InvalidOperationException("no GridObject on " + uid + " (shape unknown)");

            HashSet<string> extraFree = new HashSet<string>();
            for (int i = 0; i < oldCells.Count; i++) extraFree.Add(oldCells[i]);

            string wantCell = MiniJson.Str(pc.Action, "cell", "");
            Vector3 target = im.transform.position;
            bool hasTarget = false;
            if (wantCell.Length > 0)
            {
                Vector2Int wc;
                if (!TryParseCell(wantCell, out wc))
                    throw new InvalidOperationException("bad cell: " + wantCell);
                GridSquare wgs = BagSquareAt(wc);
                if (wgs == null)
                    throw new InvalidOperationException("no bag square at " + wantCell);
                target = wgs.transform.position;
                hasTarget = true;
            }
            else
            {
                List<Vector3> fits = FitTargets(im, go, extraFree);
                if (fits.Count > 0)
                {
                    target = fits[_placeCursor++ % fits.Count];
                    hasTarget = true;
                }
            }

            // EndDrag 之前先验证 footprint；不合法就 EndDrag 回原位再抛错。
            // 占用用 `gridPositions` 算（自己已经 StartDrag 摘下来了，所以不用再排除自己）；
            // 不用 `SquareOpen`——`containsItem` 会滞后，会把「本来空着」的格子判成占用。
            List<Vector2Int> fp = PredictCells(im, go, target);
            string bad = null;
            if (!hasTarget) bad = "no fitting cell";
            else bad = CheckFootprint(fp, BagCellSet(), OccupiedCells(uid, null));

            if (bad != null)
            {
                // 回原位：StartDrag 已经存了 startPosition/startRotation
                im.transform.rotation = beforeRot;
                im.transform.position = beforePos;
                World.CallPrivate(im, "EndDrag", null);
                throw new InvalidOperationException(type + " aborted, item restored (" + bad + ")");
            }

            im.transform.position = target;
            bool okPos = World.SetField(im, "startPosition", im.transform.position);
            bool okRot = World.SetField(im, "startRotation", im.transform.rotation);
            World.CallPrivate(im, "EndDrag", null);

            // 旧格子的 containsItem 会滞后，没被别人占着就手工清
            RefreshOccupancy();

            Vector2Int landed = CellOfPosition(go, target);
            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("action", type);
            w.Prop("uid", uid);
            w.Prop("target_cell", landed.x + "," + landed.y);
            w.Prop("start_position_set", okPos);
            w.Prop("start_rotation_set", okRot);
            w.Prop("rotated", type == "rotate_item");
            w.Name("old_cells").BeginArray();
            for (int i = 0; i < oldCells.Count; i++) w.Value(oldCells[i]);
            w.EndArray();
            w.Name("cells").BeginArray();
            List<string> got = ItemCells(it);
            for (int i = 0; i < got.Count; i++) w.Value(got[i]);
            w.EndArray();
            w.Prop("parent", it.parentInventoryGrid == null ? "" : it.parentInventoryGrid.name);
            w.Prop("in_bag", ItemInBag(uid));
            w.Prop("free_squares", CountFreeSquares());
            w.EndObject();
            pc.DiagJson = w.ToString();
        }

        /// <summary>
        /// 丢弃：StartDrag 摘出背包 → transform 放到背包外 → EndDrag。
        ///
        /// `AddToGrid` 在包外也会把 `inGrid` 置 true、`objsInGrid +1`（就是 v8.5 那个
        /// 「放包外还报成功」的老问题），所以 EndDrag 之后按 `RemoveFromGrid` 的语义纠正：
        /// `inGrid=false / parentInventoryGrid=null / gridPositions 清空 / objsInGrid-1`
        /// 纠正后物品就是 loose，离开房间会被游戏清掉（等同于丢弃）。
        /// </summary>
        private static void DropItemAction(PendingCommand pc, Item2 it, ItemMovement im,
                                           List<string> oldCells)
        {
            World.SetField(im, "isStoppingDrag", false);
            im.StartDrag();
            if (!im.isDragging)
                throw new InvalidOperationException(
                    "StartDrag refused: " + StartDragBlocked(im));

            Vector3 drop = DropPosition();
            im.transform.position = drop;
            World.SetField(im, "startPosition", im.transform.position);
            World.SetField(im, "startRotation", im.transform.rotation);
            World.CallPrivate(im, "EndDrag", null);

            it.parentInventoryGrid = null;
            it.lastParentInventoryGrid = null;
            try
            {
                GridObject go = it.gridObject;
                if (go != null) go.ClearGridPositions();
            }
            catch { }
            im.inGrid = false;
            im.inPouch = false;

            int objBefore = -1;
            int objAfter = -1;
            try
            {
                GameManager gm = GameManager.main;
                if (gm != null)
                {
                    objBefore = gm.objsInGrid;
                    if (gm.objsInGrid > 0) gm.objsInGrid = gm.objsInGrid - 1;
                    objAfter = gm.objsInGrid;
                }
            }
            catch { }

            RefreshOccupancy();

            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("action", "drop_item");
            w.Prop("uid", MiniJson.Str(pc.Action, "uid", ""));
            w.Prop("dropped_at", drop.x.ToString("0.##") + "," + drop.y.ToString("0.##"));
            w.Name("old_cells").BeginArray();
            for (int i = 0; i < oldCells.Count; i++) w.Value(oldCells[i]);
            w.EndArray();
            w.Prop("cells_left", ItemCells(it).Count);
            w.Prop("parent", it.parentInventoryGrid == null ? "" : it.parentInventoryGrid.name);
            w.Prop("in_grid_flag", im.inGrid);
            w.Prop("objs_in_grid_before", objBefore);
            w.Prop("objs_in_grid_after", objAfter);
            w.Prop("in_bag", ItemInBag(MiniJson.Str(pc.Action, "uid", "")));
            w.Prop("free_squares", CountFreeSquares());
            w.EndObject();
            pc.DiagJson = w.ToString();
        }

        /// <summary>
        /// `StartDrag()` 被拒时到底卡在哪一道门。IL（ItemMovement.StartDrag）的判定顺序：
        ///   isStoppingDrag / GameManager.dead / GameFlowManager 为 null /
        ///   !IsComplete() 且 inventoryPhase != 3 / itemMovementManager.isRunning /
        ///   isCheckingForPuzzle / GameManager.draggingItem != null
        /// 只读，用来把「swap aborted」这种错误变成能直接定位的信息。
        /// </summary>
        public static string StartDragBlocked(ItemMovement im)
        {
            List<string> parts = new List<string>();
            try
            {
                GameManager gm = GameManager.main;
                if (gm == null) parts.Add("GameManager=null");
                else
                {
                    if (gm.dead) parts.Add("dead=true");
                    parts.Add("inventoryPhase=" + (int)gm.inventoryPhase);
                    object di = World.Field(gm, "draggingItem");
                    UnityEngine.Object dio = di as UnityEngine.Object;
                    if (dio != null) parts.Add("draggingItem=" + dio.name);
                }
            }
            catch { }
            try
            {
                GameFlowManager gf = GameFlowManager.main;
                if (gf == null) parts.Add("GameFlowManager=null");
                else
                {
                    parts.Add("battlePhase=" + (int)gf.battlePhase);
                    parts.Add("IsComplete=" + gf.IsComplete());
                    parts.Add("isCheckingEffects=" + gf.isCheckingEffects);
                }
            }
            catch { }
            try
            {
                if (im != null)
                {
                    parts.Add("isStoppingDrag=" + World.Field(im, "isStoppingDrag"));
                    parts.Add("isDragging=" + im.isDragging);
                    object mgr = World.Field(im, "itemMovementManager");
                    if (mgr != null)
                    {
                        parts.Add("isRunning=" + World.Field(mgr, "isRunning"));
                        parts.Add("isCheckingForPuzzle=" + World.Field(mgr, "isCheckingForPuzzle"));
                    }
                }
            }
            catch { }
            return parts.Count == 0 ? "unknown" : string.Join(",", parts.ToArray());
        }

        /// <summary>
        /// `place_item` 那一套顺序：StartDrag → 移 transform → 改 startPosition → EndDrag。
        /// 返回 null 表示成功，否则是错误原因（调用方负责回滚）。
        /// </summary>
        private static string MoveTo(ItemMovement im, Vector3 target)
        {
            World.SetField(im, "isStoppingDrag", false);
            im.StartDrag();
            if (!im.isDragging)
                return "StartDrag refused: " + StartDragBlocked(im);
            im.transform.position = target;
            World.SetField(im, "startPosition", im.transform.position);
            World.SetField(im, "startRotation", im.transform.rotation);
            World.CallPrivate(im, "EndDrag", null);
            return null;
        }

        /// <summary>
        /// 把一件**已经 LiftFromGrid 过**的物品落到 target：改 transform → `AddToGrid`。
        ///
        /// `AddToGrid` 是 public，正是 `EndDrag` 内部最后调的那一步（SnapToGrid + 占格判定 +
        /// 设 parent + objsInGrid+1），所以对一件「已经不在格子里」的物品来说，
        /// 「直接 AddToGrid」和「StartDrag→取样→EndDrag」等价，但**不需要拖拽态**。
        /// swap 的第二步必须这样走：同一条命令里第二次 `StartDrag` 会被
        /// `GameManager.draggingItem`（还指着上一件）挡掉——真机实测。
        /// </summary>
        private static string PlaceLifted(Item2 it, ItemMovement im, Vector3 target, Quaternion rot)
        {
            if (it == null || im == null) return "no item";
            GridObject go = ResolveGridObject(it, im);
            if (go == null) return "no GridObject (shape unknown)";
            try { go.ClearGridPositions(); } catch { }
            im.transform.position = target;
            im.transform.rotation = rot;
            im.AddToGrid(false);
            bool ok = false;
            try { ok = im.inGrid; } catch { }
            if (!ok) return "AddToGrid left inGrid=false";
            return null;
        }

        /// <summary>
        /// 把物品放回「原位置 + 原朝向」（swap 回滚用）。走同一条手工路径：
        /// 先摘格子（在格的才扣 objsInGrid），再 AddToGrid 登记回去。
        /// </summary>
        private static void RestoreItem(Item2 it, ItemMovement im, Vector3 pos, Quaternion rot)
        {
            if (im == null) return;
            try
            {
                GridObject go = ResolveGridObject(it, im);
                if (go != null) go.ClearGridPositions();
                bool wasInGrid = false;
                try { wasInGrid = im.inGrid; } catch { }
                if (wasInGrid)
                {
                    GameManager gm = GameManager.main;
                    if (gm != null && gm.objsInGrid > 0) gm.objsInGrid = gm.objsInGrid - 1;
                }
                im.transform.position = pos;
                im.transform.rotation = rot;
                im.AddToGrid(false);
            }
            catch { }
        }

        /// <summary>
        /// 把物品从背包里「逻辑上」摘下来：清 gridPositions / parentInventoryGrid=null /
        /// inGrid=false / objsInGrid-1。也就是 `RemoveFromGrid` 的必要效果。
        ///
        /// **为什么不直接调 `RemoveFromGrid()`**：实测它会走 `isCheckingEffects` 那条分支
        /// （启动一个 `RemoveAtEnd()` 协程），协程把 `ItemMovementManager.isRunning` 置真，
        /// 于是同一帧里紧接着的 `StartDrag()` 全部被拒——
        /// `swap aborted (StartDrag refused: ...,isRunning=True)`。
        /// 这条自建路径只动状态、不起协程，`drop_item` 早就在用同一套语义。
        /// </summary>
        private static string LiftFromGrid(Item2 it, ItemMovement im)
        {
            if (it == null || im == null) return "no item";
            GridObject go = ResolveGridObject(it, im);
            if (go == null) return "no GridObject (shape unknown)";
            try { go.ClearGridPositions(); } catch { }
            try { it.parentInventoryGrid = null; } catch { }
            im.inGrid = false;
            im.inPouch = false;
            try
            {
                GameManager gm = GameManager.main;
                if (gm != null && gm.objsInGrid > 0) gm.objsInGrid = gm.objsInGrid - 1;
            }
            catch { }
            RefreshOccupancy();
            return null;
        }

        /// <summary>
        /// 两件互换（swap）——**必须一条动作里做完**，因为中间态跨动作表达不了：
        ///
        ///   A 在 cellsA、B 在 cellsB，背包可能一个空格都没有。
        ///   1) `LiftFromGrid(A)`：把 A 从背包里摘下来 → **A 的旧格算空**（关键中间态）
        ///   2) 把 B 放到 A 的旧位置（占用 = 所有 inGrid 物品的 gridPositions，A 已不在其中）
        ///   3) 把 A 放到 B 的旧位置（占用现在含 B 的新位置；B 的旧格已经空出来）
        ///
        /// 第 1 步不能用 `StartDrag` 代替：`StartDrag` 在 `GameManager.draggingItem != null`
        /// 时直接 return（IL IL_006a），也就是说**同一时刻只允许一件物品处于拖拽态**。
        /// 也不能用 `RemoveFromGrid`：它会起协程把 `itemMovementManager.isRunning` 置真，
        /// 同一帧里后面的 StartDrag 就全被拒。所以用自建的 `LiftFromGrid`（同一套状态语义）。
        ///
        /// 任一步不合法就回滚（B 先回原位、A 再回原位），不留半截状态。
        /// 落点由 agent 的 planner 用 `probe_fit` 算好传进来（`a_cell/b_cell` + `a_rot/b_rot`）；
        /// 不给就落到对方的锚点（同形状时等于真正的互换）。
        /// </summary>
        private static void SwapItemsAction(PendingCommand pc)
        {
            string uidA = MiniJson.Str(pc.Action, "a", "");
            string uidB = MiniJson.Str(pc.Action, "b", "");
            if (uidA.Length == 0 || uidB.Length == 0)
                throw new InvalidOperationException("swap_items needs a and b");
            if (uidA == uidB) throw new InvalidOperationException("swap_items a == b");

            Item2 a = FindItem(uidA);
            Item2 b = FindItem(uidB);
            if (a == null) throw new InvalidOperationException("item not found: " + uidA);
            if (b == null) throw new InvalidOperationException("item not found: " + uidB);

            ItemMovement imA = null;
            ItemMovement imB = null;
            try { imA = a.GetItemMovement(); } catch { }
            try { imB = b.GetItemMovement(); } catch { }
            if (imA == null) throw new InvalidOperationException("no ItemMovement on " + uidA);
            if (imB == null) throw new InvalidOperationException("no ItemMovement on " + uidB);

            List<string> cellsA0 = ItemCells(a);
            List<string> cellsB0 = ItemCells(b);
            if (cellsA0.Count == 0) throw new InvalidOperationException("not in bag: " + uidA);
            if (cellsB0.Count == 0) throw new InvalidOperationException("not in bag: " + uidB);

            int freeBefore = CountFreeSquares();
            int gridBefore = CountGridSquares();

            // 朝向角（needs 与 footprint 都按它算；StartDrag 会归整到 90° 的倍数）
            int eulerA0 = EulerZ(imA);
            int eulerB0 = EulerZ(imB);
            RotateItemTo(imA, eulerA0);
            RotateItemTo(imB, eulerB0);

            Vector3 posA0 = imA.transform.position;
            Quaternion rotA0 = imA.transform.rotation;
            Vector3 posB0 = imB.transform.position;
            Quaternion rotB0 = imB.transform.rotation;

            GridObject goA = ResolveGridObject(a, imA);
            GridObject goB = ResolveGridObject(b, imB);
            if (goA == null) throw new InvalidOperationException("no GridObject on " + uidA);
            if (goB == null) throw new InvalidOperationException("no GridObject on " + uidB);

            HashSet<string> bag = BagCellSet();

            // ---- 落点 + 朝向 ----
            bool hasCellA = false, hasCellB = false;
            Vector3 targetA = posB0;
            Vector3 targetB = posA0;
            string wantA = MiniJson.Str(pc.Action, "a_cell", "");
            string wantB = MiniJson.Str(pc.Action, "b_cell", "");
            if (wantB.Length > 0)
            {
                Vector2Int c;
                if (!TryParseCell(wantB, out c))
                    throw new InvalidOperationException("bad b_cell: " + wantB);
                GridSquare gs = BagSquareAt(c);
                if (gs == null) throw new InvalidOperationException("no bag square at " + wantB);
                targetB = gs.transform.position;
                hasCellB = true;
            }
            if (wantA.Length > 0)
            {
                Vector2Int c;
                if (!TryParseCell(wantA, out c))
                    throw new InvalidOperationException("bad a_cell: " + wantA);
                GridSquare gs = BagSquareAt(c);
                if (gs == null) throw new InvalidOperationException("no bag square at " + wantA);
                targetA = gs.transform.position;
                hasCellA = true;
            }
            int rotAg = (int)MiniJson.Num(pc.Action, "a_rot", int.MinValue);
            int rotBg = (int)MiniJson.Num(pc.Action, "b_rot", int.MinValue);
            if (rotAg != int.MinValue) RotateItemTo(imA, rotAg);
            if (rotBg != int.MinValue) RotateItemTo(imB, rotBg);

            // ---- 1) 把 A 从背包里摘下来（A 的旧格从此算空） ----
            // 不能用 StartDrag：同一时刻只允许一件物品处于拖拽态（GameManager.draggingItem）。
            // 也不能用 RemoveFromGrid：它会起协程、把 itemMovementManager.isRunning 置真，
            // 同一帧里后面的 StartDrag 就全被拒了（真机实测）。见 LiftFromGrid。
            string liftErr = LiftFromGrid(a, imA);
            if (liftErr != null)
            {
                RefreshOccupancy();
                throw new InvalidOperationException(
                    "swap aborted: cannot lift " + uidA + " (" + liftErr + ")");
            }
            bool aStillInGrid = false;
            try { aStillInGrid = imA.inGrid; } catch { }
            if (aStillInGrid)
            {
                RefreshOccupancy();
                throw new InvalidOperationException(
                    "swap aborted: lift did not detach " + uidA);
            }

            // ---- 2) B -> A 的旧位置 ----
            HashSet<string> occ1 = OccupiedCells(uidA, null);
            string badB = CheckFootprint(PredictCells(imB, goB, targetB), bag, occ1);
            if (badB != null)
            {
                RestoreItem(a, imA, posA0, rotA0);   // A 回原位
                RefreshOccupancy();
                throw new InvalidOperationException("swap aborted, nothing moved (" + uidB +
                    " -> " + (hasCellB ? wantB : "A-anchor") + ": " + badB + ")");
            }

            string badMoveB = MoveTo(imB, targetB);
            if (badMoveB != null)
            {
                RestoreItem(a, imA, posA0, rotA0);
                RefreshOccupancy();
                throw new InvalidOperationException("swap aborted (" + uidB + ": " +
                    badMoveB + ")");
            }
            RefreshOccupancy();

            // ---- 3) A -> B 的旧位置 ----
            HashSet<string> occ2 = OccupiedCells(uidA, null);
            string badA = CheckFootprint(PredictCells(imA, goA, targetA), bag, occ2);
            if (badA != null)
            {
                // 回滚：B 先回原位（B 的旧格空着，A 还没进包），A 再回原位
                RestoreItem(b, imB, posB0, rotB0);
                RestoreItem(a, imA, posA0, rotA0);
                RefreshOccupancy();
                throw new InvalidOperationException("swap aborted, both restored (" + uidA +
                    " -> " + (hasCellA ? wantA : "B-anchor") + ": " + badA + ")");
            }

            // 这里**不能**再用 StartDrag：同一条命令里第二次拖动会被 draggingItem 挡掉。
            string badPlaceA = PlaceLifted(a, imA, targetA, imA.transform.rotation);
            if (badPlaceA != null)
            {
                RestoreItem(b, imB, posB0, rotB0);
                RestoreItem(a, imA, posA0, rotA0);
                RefreshOccupancy();
                throw new InvalidOperationException("swap aborted, both restored (" + uidA +
                    ": " + badPlaceA + ")");
            }
            RefreshOccupancy();

            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("action", "swap_items");
            w.Prop("a", uidA);
            w.Prop("b", uidB);
            w.Name("a_cells_before").BeginArray();
            for (int i = 0; i < cellsA0.Count; i++) w.Value(cellsA0[i]);
            w.EndArray();
            w.Name("b_cells_before").BeginArray();
            for (int i = 0; i < cellsB0.Count; i++) w.Value(cellsB0[i]);
            w.EndArray();
            w.Name("a_cells").BeginArray();
            List<string> aCells1 = ItemCells(a);
            for (int i = 0; i < aCells1.Count; i++) w.Value(aCells1[i]);
            w.EndArray();
            w.Name("b_cells").BeginArray();
            List<string> bCells1 = ItemCells(b);
            for (int i = 0; i < bCells1.Count; i++) w.Value(bCells1[i]);
            w.EndArray();
            w.Prop("a_in_bag", ItemInBag(uidA));
            w.Prop("b_in_bag", ItemInBag(uidB));
            w.Prop("free_squares_before", freeBefore);
            w.Prop("free_squares", CountFreeSquares());
            w.Prop("grid_squares_before", gridBefore);
            w.Prop("grid_squares", CountGridSquares());
            w.EndObject();
            pc.DiagJson = w.ToString();
        }

        /// <summary>transform 的 z 角，归一到 [0,360)。</summary>
        private static int EulerZ(ItemMovement im)
        {
            if (im == null) return 0;
            try { return ((int)Mathf.Round(im.transform.eulerAngles.z) % 360 + 360) % 360; }
            catch { return 0; }
        }

        /// <summary>
        /// 触发当前房间的事件（通用入口）。`Chest` 有自己的 `open_chest`，这个主要是给
        /// **SimpleClickItem**（地图上摆着的东西）用的：`start_event` 对它是一个空操作。
        ///
        /// 事件流（IL 逐步核对过，v8.17）：
        ///   1. `DungeonEvent.Play()` 实例化 `itemsToSpawn`，把 `SimpleClickItem.dungeonEvent`
        ///      绑上去。**Play 自己是幂等的**：开头 `if (GetEventProperty(open)) return;`，
        ///      生成前先 `AddEventProperty(open, 0)`。所以「Play 会重复生成」不是游戏的锅，
        ///      是老代码每次都先 `RemoveEventProperty(open)` 再 Play（E15 的根因，已修）。
        ///   2. `SimpleClickItem.Start()`（**下一帧**才跑）从 `dungeonEvent.GetItems()` 里取出
        ///      事件底下挂着的真·战利品（`Item2` 子物体），把它们 **SetActive(true)** 并
        ///      摆到祭坛前面 —— 也就是说「东西变成 loose」发生在 Start 之后的那一帧。
        ///   3. `SimpleClickItem.Click()` 做的是：动画 + `dungeonEvent.FinishEvent()` +
        ///      `StartCoroutine(TakeAction())`。`TakeAction` 只处理血量/经验/金币/地牢属性
        ///      这些效果；`type=item(4)` 它**什么都不做**（东西本来就摆在祭坛前，等玩家拖进包）。
        ///
        /// 所以正确的调用序列是 `play_event{click:false}` → 等一两帧 → `play_event{play:false}`，
        /// 而不是把 Play + Click 挤在同一帧。
        ///
        /// 参数：
        ///   uid          事件 uid（`d123`）；省略则取当前房间里「没结束」的第一个，
        ///                都结束了就取房间里的第一个（方便事后点它剩下的 SimpleClickItem）。
        ///   play=bool    默认 true。已经 open/ finished 的事件不再 Play（幂等）。
        ///   click=bool   默认 true。只点 **isOpen=false** 的 SimpleClickItem，点过的不会重复点。
        /// </summary>
        private static void PlayEventAction(PendingCommand pc)
        {
            string uid = MiniJson.Str(pc.Action, "uid", "");
            bool wantPlay = MiniJson.Bool(pc.Action, "play", true);
            bool wantClick = MiniJson.Bool(pc.Action, "click", true);

            DungeonEvent ev = FindEventForAction(uid);
            if (ev == null)
                throw new InvalidOperationException(uid.Length > 0
                    ? "event not found: " + uid
                    : "no event in the current room");

            bool openBefore = EventOpenProp(ev);
            bool finishedBefore = EventFinished(ev);
            int looseBefore = World.CountLoose();
            int inGridBefore = World.CountInGrid();
            int spawnedBefore = CountClickItemsOf(ev);

            bool played = false;
            string skip = "";
            if (wantPlay)
            {
                // 幂等：`open` 是 Play 自己打的标记，已经打过就别再调（再调会多生成一份）。
                if (finishedBefore) skip = "finished";
                else if (openBefore) skip = "already_open";
                else { World.CallPrivate(ev, "Play", null); played = true; }
            }
            else skip = "play=false";

            int spawned = 0;
            int clicked = 0;
            string clickError = "";
            try
            {
                List<SimpleClickItem> scis = ClickItemsOf(ev);
                spawned = scis.Count;
                for (int i = 0; wantClick && i < scis.Count; i++)
                {
                    SimpleClickItem sci = scis[i];
                    if (sci == null) continue;
                    object open = World.Field(sci, "isOpen");
                    if (open is bool && (bool)open) continue;   // 点过了，再点也没用
                    try { sci.Click(); clicked++; }
                    catch (Exception ex) { if (clickError.Length == 0) clickError = ex.GetType().Name + ": " + ex.Message; }
                }
            }
            catch (Exception ex)
            {
                if (clickError.Length == 0) clickError = ex.GetType().Name + ": " + ex.Message;
            }

            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("action", "play_event");
            w.Prop("event", "d" + ObjId(ev));
            w.Prop("type", EventType(ev));
            w.Prop("play_requested", wantPlay);
            w.Prop("click_requested", wantClick);
            w.Prop("played", played);
            w.Prop("play_skip", skip);
            w.Prop("open_before", openBefore);
            w.Prop("finished_before", finishedBefore);
            w.Prop("open_after", EventOpenProp(ev));
            w.Prop("finished_after", EventFinished(ev));
            w.Prop("items_to_spawn", EventItemsToSpawn(ev));
            w.Prop("click_items_before", spawnedBefore);
            w.Prop("click_items_now", spawned);
            w.Prop("clicked", clicked);
            if (clickError.Length > 0) w.Prop("click_error", clickError);
            w.Prop("loose_before", looseBefore);
            w.Prop("loose_now", World.CountLoose());
            w.Prop("in_grid_before", inGridBefore);
            w.Prop("in_grid_now", World.CountInGrid());
            w.Prop("free_squares", CountFreeSquares());

            // 事件底下挂着的物品（Play 之后它们是 SCI.Start() 里被激活的那批）
            w.Name("event_items").BeginArray();
            try
            {
                Transform tf = ev.transform;
                for (int i = 0; i < tf.childCount; i++)
                {
                    Item2 it = tf.GetChild(i).GetComponent<Item2>();
                    if (it != null) WriteItemBrief(w, it);
                }
            }
            catch { }
            w.EndArray();

            w.Name("click_items").BeginArray();
            try
            {
                List<SimpleClickItem> scis2 = ClickItemsOf(ev);
                for (int i = 0; i < scis2.Count; i++) WriteSciDiag(w, scis2[i]);
            }
            catch { }
            w.EndArray();
            w.EndObject();
            pc.DiagJson = w.ToString();
        }

        // ---------- SimpleClickItem / 地上物品的诊断小件 ----------

        /// <summary>
        /// 命令要操作的那个事件：给了 uid 就按 uid 找（找不到返回 null），
        /// 没给就取当前房间里「没结束」的第一个，都结束了就取第一个。
        /// </summary>
        private static DungeonEvent FindEventForAction(string uid)
        {
            if (uid != null && uid.Length > 0)
            {
                List<DungeonEvent> all = null;
                try { all = DungeonEvent.FindAllDungeonEvents(); } catch { }
                if (all == null) { try { all = DungeonEvent.dungeonEvents; } catch { } }
                for (int i = 0; all != null && i < all.Count; i++)
                {
                    if (all[i] == null) continue;
                    if ("d" + ObjId(all[i]) == uid) return all[i];
                }
                return null;
            }

            List<DungeonEvent> evs = EventsInRoom(CurrentRoom());
            for (int i = 0; i < evs.Count; i++)
                if (evs[i] != null && !EventFinished(evs[i])) return evs[i];
            for (int i = 0; i < evs.Count; i++)
                if (evs[i] != null) return evs[i];
            return null;
        }

        /// <summary>
        /// `play_event` 点完之后会不会真的掉东西到地上？
        /// 只有 effect 里出现 `type=4`（item）才会——`QuickBoost HP/Gold` 这类
        /// 只改血量/金币，等「loot 出现」只会白等到超时（真机踩过）。
        /// </summary>
        internal static bool PlayEventExpectsLoot(Dictionary<string, object> action)
        {
            try
            {
                DungeonEvent ev = FindEventForAction(MiniJson.Str(action, "uid", ""));
                if (ev == null) return false;
                List<SimpleClickItem> scis = ClickItemsOf(ev);
                for (int i = 0; i < scis.Count; i++)
                {
                    System.Collections.IList effects = World.Field(scis[i], "effects") as System.Collections.IList;
                    for (int k = 0; effects != null && k < effects.Count; k++)
                    {
                        object t = World.Field(effects[k], "type");
                        if (t != null && Convert.ToInt32(t) == 4) return true;
                    }
                }
            }
            catch { }
            return false;
        }

        /// <summary>绑定到这个事件上的所有 SimpleClickItem（含未激活的）。</summary>
        private static List<SimpleClickItem> ClickItemsOf(DungeonEvent ev)
        {
            List<SimpleClickItem> outList = new List<SimpleClickItem>();
            if (ev == null) return outList;
            try
            {
                UnityEngine.Object[] objs = UnityEngine.Object.FindObjectsOfType(typeof(SimpleClickItem));
                for (int i = 0; objs != null && i < objs.Length; i++)
                {
                    SimpleClickItem sci = objs[i] as SimpleClickItem;
                    if (sci == null) continue;
                    DungeonEvent owner = World.Field(sci, "dungeonEvent") as DungeonEvent;
                    if (owner != ev) continue;
                    outList.Add(sci);
                }
            }
            catch { }
            return outList;
        }

        private static int CountClickItemsOf(DungeonEvent ev)
        {
            return ClickItemsOf(ev).Count;
        }

        private static void WriteItemBrief(JsonWriter w, Item2 it)
        {
            w.BeginObject();
            if (it == null) { w.Prop("name", ""); w.EndObject(); return; }
            w.Prop("name", it.name);
            w.Prop("uid", "i" + ObjId(it));
            w.Prop("display", it.displayName == null ? "" : it.displayName);
            bool active = false, activeHier = false;
            try { active = it.gameObject.activeSelf; activeHier = it.gameObject.activeInHierarchy; } catch { }
            w.Prop("active", active);
            w.Prop("active_in_hierarchy", activeHier);
            w.Prop("destroyed", it.destroyed);
            bool inGrid = false;
            try { inGrid = it.itemMovement != null && it.itemMovement.inGrid; } catch { }
            w.Prop("in_grid", inGrid);
            bool inAll = false;
            try { inAll = Item2.allItems != null && Item2.allItems.Contains(it); } catch { }
            w.Prop("in_all_items", inAll);
            string types = "";
            try
            {
                if (it.itemType != null)
                    for (int i = 0; i < it.itemType.Count; i++)
                        types += (i == 0 ? "" : "|") + it.itemType[i].ToString();
            }
            catch { }
            w.Prop("item_type", types);
            string parent = "";
            try { parent = it.transform.parent == null ? "" : it.transform.parent.name; } catch { }
            w.Prop("parent", parent);
            w.EndObject();
        }

        private static void WriteSciDiag(JsonWriter w, SimpleClickItem sci)
        {
            w.BeginObject();
            if (sci == null) { w.Prop("uid", ""); w.EndObject(); return; }
            w.Prop("uid", "s" + ObjId(sci));
            w.Prop("name", sci.name);
            bool active = false, activeHier = false;
            try { active = sci.gameObject.activeSelf; activeHier = sci.gameObject.activeInHierarchy; } catch { }
            w.Prop("active", active);
            w.Prop("active_in_hierarchy", activeHier);
            object open = World.Field(sci, "isOpen");
            w.Prop("is_open", open is bool && (bool)open);
            try
            {
                w.Prop("x", sci.transform.position.x);
                w.Prop("y", sci.transform.position.y);
            }
            catch { }
            string parent = "";
            try { parent = sci.transform.parent == null ? "" : sci.transform.parent.name; } catch { }
            w.Prop("parent", parent);

            System.Collections.IList effects = World.Field(sci, "effects") as System.Collections.IList;
            w.Prop("effect_count", effects == null ? -1 : effects.Count);
            w.Name("effects").BeginArray();
            for (int i = 0; effects != null && i < effects.Count; i++)
            {
                object ef = effects[i];
                w.BeginObject();
                object t = World.Field(ef, "type");
                w.Prop("type", t == null ? -1 : Convert.ToInt32(t));
                object n = World.Field(ef, "num");
                w.Prop("num", n == null ? 0 : Convert.ToInt32(n));
                object ao = World.Field(ef, "assignedObject");
                GameObject go = ao as GameObject;
                w.Prop("assigned", go == null ? "" : go.name);
                if (go != null)
                {
                    Item2 it = go.GetComponent<Item2>();
                    if (it != null)
                    {
                        w.Name("item");
                        WriteItemBrief(w, it);
                    }
                }
                w.EndObject();
            }
            w.EndArray();
            w.EndObject();
        }

        /// <summary>
        /// 只读：把所有 `Item2` 摊开看（活跃的 + 未激活的）。
        ///
        /// `Item2.GetAllItemsOutsideGrid()`（state 里的 `items_loose`）走的是
        /// `Item2.allItems`，**未激活的物品不在里面**——「生成出来了但看不见」和
        /// 「压根没生成」在 state 上是同一个样子。这个动作就是用来分辨这两者的。
        /// </summary>
        private static void DiagItemsAction(PendingCommand pc)
        {
            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("action", "diag_items");
            int allCount = -1;
            try { allCount = Item2.allItems == null ? -1 : Item2.allItems.Count; } catch { }
            w.Prop("all_items", allCount);
            w.Prop("in_grid", World.CountInGrid());
            w.Prop("loose", World.CountLoose());
            w.Prop("free_squares", CountFreeSquares());

            w.Name("active").BeginArray();
            try
            {
                UnityEngine.Object[] objs = UnityEngine.Object.FindObjectsOfType(typeof(Item2));
                for (int i = 0; objs != null && i < objs.Length; i++)
                    WriteItemBrief(w, objs[i] as Item2);
            }
            catch { }
            w.EndArray();

            // 未激活的：FindObjectsOfType 抓不到，只能 Resources.FindObjectsOfTypeAll，
            // 但它连 prefab 资源也一起返回，所以按 scene 有效来过滤。
            w.Name("inactive").BeginArray();
            try
            {
                UnityEngine.Object[] objs = Resources.FindObjectsOfTypeAll(typeof(Item2));
                for (int i = 0; objs != null && i < objs.Length; i++)
                {
                    Item2 it = objs[i] as Item2;
                    if (it == null) continue;
                    bool inScene = false;
                    try { inScene = it.gameObject.scene.IsValid(); } catch { }
                    if (!inScene) continue;
                    bool activeHier = false;
                    try { activeHier = it.gameObject.activeInHierarchy; } catch { }
                    if (activeHier) continue;
                    WriteItemBrief(w, it);
                }
            }
            catch { }
            w.EndArray();
            w.EndObject();
            pc.DiagJson = w.ToString();
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

            // SPIKE-07 §4 第二步：结构化战斗回执
            // 光有 delta 只能说明「数值变了」，说不清「谁打谁、挡了多少、上了什么状态」；
            // 效果不可见的物品（HEAT POTION / LUCKY STAR / REVERSAL AMULET）就永远学不到
            w.Name("receipt");
            Receipt.Write(w, pc, after);

            // 只读诊断（diag_event / open_chest）的原始数据
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


