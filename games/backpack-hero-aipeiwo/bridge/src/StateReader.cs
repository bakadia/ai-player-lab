// 把游戏对象图读成 `bph.state.v1`。
//
// 原则：
// 1. 只读，不 patch、不改游戏状态。
// 2. 每个字段单独 try/catch —— 某个字段读不到，整份 state 不能跟着废掉。
// 3. 先取到局部变量，再写 JSON —— 保证输出永远是完整合法的 JSON，
//    不会因为中途抛异常留下半个对象。
// 4. 未知/待验证的字段（比如 Enemy.health 和 stats.health 的关系）两个都输出，
//    交给 Probe() 打日志去比对。

using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace BphBridge
{
    internal static class StateReader
    {
        private const int MaxEnemies = 12;
        private const int MaxItems = 40;

        // ---------- 取值兜底 ----------

        private static int I(Func<int> f, int fallback)
        {
            try { return f(); } catch { return fallback; }
        }

        private static bool B(Func<bool> f, bool fallback)
        {
            try { return f(); } catch { return fallback; }
        }

        private static string S(Func<string> f)
        {
            try { string v = f(); return v == null ? "" : v; } catch { return ""; }
        }

        // ---------- 主入口 ----------

        /// <summary>生成完整 state 文档。seq/ts 由调用方给，便于「内容不变就不重写」。</summary>
        public static string Build(long seq, long ts)
        {
            GameManager gm = null;
            Player pl = null;
            GameFlowManager gf = null;
            try { gm = GameManager.main; } catch { }
            try { pl = Player.main; } catch { }
            try { gf = GameFlowManager.main; } catch { }

            string scene = S(delegate { return SceneManager.GetActiveScene().name; });

            int battlePhase = -1;
            try { if (gf != null) battlePhase = (int)gf.battlePhase; } catch { }

            int invPhase = -1;
            try { if (gm != null) invPhase = (int)gm.inventoryPhase; } catch { }

            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("schema", "bph.state.v1");
            w.Prop("seq", seq);
            w.Prop("ts", ts);
            w.Prop("scene", scene);
            w.Prop("mode", DeriveMode(gm, pl, battlePhase));
            w.Prop("battle_phase", battlePhase);
            w.Prop("inventory_phase", invPhase);

            // ---- run ----
            w.Name("run").BeginObject();
            w.Prop("floor", I(delegate { return gm.floor; }, -1));
            w.Prop("turn", I(delegate { return gf.turnNumber; }, -1));
            w.Prop("gold", I(delegate { return gm.goldAmount; }, -1));
            w.Prop("level", I(delegate { return pl.level; }, -1));
            w.Prop("hero", S(delegate { return pl.characterName.ToString(); }));
            w.EndObject();

            // ---- player ----
            Status pst = null;
            try { if (pl != null) pst = pl.stats; } catch { }
            w.Name("player").BeginObject();
            w.Prop("hp", pst == null ? -1 : I(delegate { return pst.health; }, -1));
            w.Prop("max_hp", pst == null ? -1 : I(delegate { return pst.maxHealth; }, -1));
            w.Prop("block", pst == null ? -1 : I(delegate { return pst.armor; }, -1));
            w.Prop("energy", I(delegate { return pl.AP; }, -1));
            w.Prop("max_energy", I(delegate { return pl.APperTurn; }, -1));
            // 注：GameManager.currentHealthAmount 在 SPIKE-02 里被证伪——
            // 玩家 HP 掉到 30 时它仍然是 40，不是活值。只在 Probe 里留作对照。
            w.EndObject();

            // ---- enemies ----
            int playerEnergy = I(delegate { return pl.AP; }, -1);
            bool inPlayerTurn = battlePhase == 1;
            List<LegalUse> legalUses = new List<LegalUse>();
            List<Enemy> allEnemies = null;
            try { allEnemies = Enemy.allEnemies; } catch { }

            int enemiesTotal = 0;
            w.Name("enemies").BeginArray();
            if (allEnemies != null)
            {
                int written = 0;
                for (int i = 0; i < allEnemies.Count; i++)
                {
                    Enemy e = allEnemies[i];
                    if (e == null) continue;
                    if (B(delegate { return e.dead; }, true)) continue;
                    enemiesTotal++;
                    if (written >= MaxEnemies) continue;

                    // 先全部取出来，再写
                    string ename = S(delegate { return e.displayName; });
                    Status est = null;
                    try { est = e.stats; } catch { }
                    int ehp = est == null ? -1 : I(delegate { return est.health; }, -1);
                    int emax = est == null ? -1 : I(delegate { return est.maxHealth; }, -1);
                    int eblock = est == null ? -1 : I(delegate { return est.armor; }, -1);
                    int rawHealth = I(delegate { return e.health; }, -1);
                    bool targetable = B(delegate { return e.isTargetable; }, false);
                    bool pet = B(delegate { return e.isPet; }, false);

                    w.BeginObject();
                    w.Prop("uid", "e" + written);
                    w.Prop("name", ename);
                    w.Prop("hp", ehp);
                    w.Prop("max_hp", emax);
                    w.Prop("block", eblock);
                    w.Prop("raw_health", rawHealth);   // 待验证：和 hp 哪个是活值
                    w.Prop("targetable", targetable);
                    w.Prop("is_pet", pet);

                    w.Name("attacks").BeginArray();
                    Enemy.PossibleAttack pa = null;
                    try { pa = e.myNextAttack; } catch { }
                    List<Enemy.Attack> atks = null;
                    try { if (pa != null) atks = pa.attacks; } catch { }
                    if (atks != null)
                    {
                        for (int k = 0; k < atks.Count && k < 4; k++)
                        {
                            Enemy.Attack a = atks[k];
                            if (a == null) continue;
                            int atype = I(delegate { return (int)a.type; }, -1);
                            int apower = I(delegate { return a.currentPower; }, -1);
                            string atext = S(delegate { return a.description; });
                            bool ahide = B(delegate { return a.hideIntention; }, false);
                            w.BeginObject();
                            w.Prop("type", atype);
                            w.Prop("power", apower);
                            w.Prop("text", atext);
                            w.Prop("hidden", ahide);
                            w.EndObject();
                        }
                    }
                    w.EndArray();

                    w.EndObject();
                    written++;
                }
            }
            w.EndArray();

            // ---- backpack ----
            List<Item2> allItems = null;
            try { allItems = Item2.allItems; } catch { }

            int itemsTotal = 0;
            int itemsPlaced = 0;
            if (allItems != null)
            {
                for (int i = 0; i < allItems.Count; i++)
                {
                    Item2 it = allItems[i];
                    if (it == null) continue;
                    itemsTotal++;
                    if (IsPlaced(it)) itemsPlaced++;
                }
            }

            w.Name("backpack").BeginObject();
            w.Prop("items_total", itemsTotal);
            w.Prop("items_placed", itemsPlaced);
            w.Name("items").BeginArray();
            if (allItems != null)
            {
                int written = 0;
                for (int i = 0; i < allItems.Count && written < MaxItems; i++)
                {
                    Item2 it = allItems[i];
                    if (it == null) continue;
                    if (!IsPlaced(it)) continue;

                    string iname = S(delegate { return it.displayName; });
                    int iuid = I(delegate { return it.GetInstanceID(); }, 0);
                    int icost = I(delegate { return it.cost; }, -1);
                    int icharges = I(delegate { return it.currentCharges; }, -1);
                    int imaxcharges = I(delegate { return it.charges; }, -1);
                    int iplaytype = I(delegate { return (int)it.playType; }, -1);
                    string itext = "";

                    // SPIKE-02：item.cost 恒为 -999（哨兵），真实费用在 costs 集合里。
                    // Item2+Cost+Type: energy=0, mana=1, gold=2。
                    int energyCost = 0;
                    int manaCost = 0;
                    int goldCost = 0;
                    try
                    {
                        List<Item2.Cost> costs = it.costs;
                        if (costs != null)
                        {
                            for (int c = 0; c < costs.Count; c++)
                            {
                                Item2.Cost cost = costs[c];
                                if (cost == null) continue;
                                int t = (int)cost.type;
                                if (t == 0) energyCost += cost.currentValue;
                                else if (t == 1) manaCost += cost.currentValue;
                                else if (t == 2) goldCost += cost.currentValue;
                            }
                        }
                    }
                    catch { }
                    try
                    {
                        List<string> ds = it.descriptions;
                        if (ds != null && ds.Count > 0) itext = ds[0] == null ? "" : ds[0];
                    }
                    catch { }

                    // ---- 合法性与原因 ----
                    // 主判据用游戏自己的 Item2.CanBeUsedActive()，不再靠手写启发式。
                    // 起因：合金(ALLOY) 是遗物（被动），playType 却是 Active(0)、
                    // energy_cost=0，被旧规则误判成「可用动作」，用了什么都不发生。
                    bool isRelic = IsRelic(it);
                    int canActive = -1;
                    if (inPlayerTurn) canActive = TryCanUseActive(it);

                    string reason = null;
                    bool usable = false;
                    if (!inPlayerTurn) reason = "not_player_turn";
                    else if (isRelic) reason = "relic_passive";
                    else if (canActive == 0) reason = "game_says_no";
                    else if (canActive == 1) usable = true;     // 游戏说行就信它
                    else
                    {
                        // CanBeUsedActive 调用失败，退回启发式
                        if (iplaytype != 0) reason = "not_active_item";
                        else if (imaxcharges != -999 && icharges <= 0) reason = "no_charges";
                        else if (energyCost > playerEnergy) reason = "no_energy";
                        else if (manaCost > 0) reason = "needs_mana";
                        else if (goldCost > 0) reason = "needs_gold";
                        usable = reason == null;
                    }

                    if (usable)
                    {
                        LegalUse lu = new LegalUse();
                        lu.Uid = "i" + iuid;
                        lu.Energy = energyCost;
                        legalUses.Add(lu);
                    }

                    List<string> cells = new List<string>();
                    try
                    {
                        GridObject go = it.gridObject;
                        if (go != null && go.gridPositions != null)
                        {
                            for (int c = 0; c < go.gridPositions.Count && c < 16; c++)
                            {
                                Vector2Int v = go.gridPositions[c];
                                cells.Add(v.x + "," + v.y);
                            }
                        }
                    }
                    catch { }

                    w.BeginObject();
                    w.Prop("uid", "i" + iuid);
                    w.Prop("name", iname);
                    w.Prop("cost", icost);
                    w.Prop("energy_cost", energyCost);
                    w.Name("costs").BeginObject();
                    w.Prop("energy", energyCost);
                    w.Prop("mana", manaCost);
                    w.Prop("gold", goldCost);
                    w.EndObject();
                    w.Prop("usable", usable);
                    w.Prop("is_relic", isRelic);
                    w.Prop("can_be_used_active", canActive);   // -1=调用失败 0=否 1=是
                    if (reason == null) w.Name("reason").Null();
                    else w.Prop("reason", reason);
                    w.Prop("charges", icharges);
                    w.Prop("max_charges", imaxcharges);
                    w.Prop("play_type", iplaytype);
                    w.Prop("text", itext);
                    w.Name("cells").BeginArray();
                    for (int c = 0; c < cells.Count; c++) w.Value(cells[c]);
                    w.EndArray();
                    w.EndObject();

                    written++;
                }
            }
            w.EndArray();
            w.EndObject();

            // ---- 战斗统计（游戏自己记的，白送的交叉校验） ----
            // CombatStat+Type: itemsUsed=0, damageTaken=1, damageDealt=6
            // CombatStat+Length: turn=0, combat=1
            w.Name("stats").BeginObject();
            w.Name("turn").BeginObject();
            w.Prop("damage_dealt", CombatStat(gf, 6, 0));
            w.Prop("damage_taken", CombatStat(gf, 1, 0));
            w.Prop("items_used", CombatStat(gf, 0, 0));
            w.EndObject();
            w.Name("combat").BeginObject();
            w.Prop("damage_dealt", CombatStat(gf, 6, 1));
            w.Prop("damage_taken", CombatStat(gf, 1, 1));
            w.Prop("items_used", CombatStat(gf, 0, 1));
            w.EndObject();
            w.EndObject();

            // ---- 合法动作（Bridge 算，Agent 不猜） ----
            w.Name("legal").BeginArray();
            if (inPlayerTurn)
            {
                for (int i = 0; i < legalUses.Count; i++)
                {
                    w.BeginObject();
                    w.Prop("type", "use_item");
                    w.Prop("uid", legalUses[i].Uid);
                    w.Prop("energy", legalUses[i].Energy);
                    w.EndObject();
                }
                w.BeginObject();
                w.Prop("type", "end_turn");
                w.EndObject();
            }
            w.EndArray();

            // ---- counters ----
            int statuses = 0;
            try { if (Status.allStatuses != null) statuses = Status.allStatuses.Count; } catch { }
            w.Name("counters").BeginObject();
            w.Prop("enemies_alive", enemiesTotal);
            w.Prop("statuses_total", statuses);
            w.Prop("enemies_in_list", allEnemies == null ? -1 : allEnemies.Count);
            w.Prop("items_in_list", allItems == null ? -1 : allItems.Count);
            w.EndObject();

            w.EndObject();
            return w.ToString();
        }

        /// <summary>Item2+ItemType.Relic = 22。遗物是被动效果，不该当动作发。</summary>
        private static bool IsRelic(Item2 it)
        {
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

        /// <summary>
        /// 问游戏自己：这件物品现在能不能主动用。
        /// 返回 1=能 0=不能 -1=调用失败（调用方退回启发式）。
        /// </summary>
        private static int TryCanUseActive(Item2 it)
        {
            try
            {
                List<Item2.Cost> costs = new List<Item2.Cost>();
                List<SpecificConditionToUse.ConditionTime> times =
                    new List<SpecificConditionToUse.ConditionTime>();
                bool ok = it.CanBeUsedActive(false, costs, true, false, times, true);
                return ok ? 1 : 0;
            }
            catch { return -1; }
        }

        /// <summary>一条合法动作（目前只有 use_item）。</summary>
        private sealed class LegalUse
        {
            public string Uid;
            public int Energy;
        }

        /// <summary>读游戏自带的战斗统计。CombatStat+Type / +Length 见注释。</summary>
        private static int CombatStat(GameFlowManager gf, int type, int length)
        {
            try
            {
                if (gf == null) return -1;
                return gf.GetCombatStat(
                    (GameFlowManager.CombatStat.Type)type,
                    (GameFlowManager.CombatStat.Length)length);
            }
            catch { return -1; }
        }

        private static bool IsPlaced(Item2 it)
        {
            try
            {
                GridObject go = it.gridObject;
                if (go == null) return false;
                List<Vector2Int> pos = go.gridPositions;
                return pos != null && pos.Count > 0;
            }
            catch { return false; }
        }

        /// <summary>
        /// mode 是启发式：SPIKE-01 §7 还没定死「非战斗」怎么区分。
        /// 原始枚举值同时输出，等看到真实数据再收窄。
        /// </summary>
        private static string DeriveMode(GameManager gm, Player pl, int battlePhase)
        {
            if (gm == null || pl == null) return "menu";
            if (battlePhase >= 1 && battlePhase <= 4) return "battle";
            int floor = I(delegate { return gm.floor; }, 0);
            if (floor <= 0) return "menu";
            return "map";
        }

        // ---------- 探针 ----------

        /// <summary>
        /// 一次性把待验证的字段打出来。用来在真实数据上一次性回答 SPIKE-01 §7。
        /// </summary>
        public static List<string> Probe()
        {
            List<string> lines = new List<string>();
            GameManager gm = null;
            Player pl = null;
            GameFlowManager gf = null;
            try { gm = GameManager.main; } catch { }
            try { pl = Player.main; } catch { }
            try { gf = GameFlowManager.main; } catch { }

            lines.Add("--- PROBE begin ---");
            lines.Add("scene=" + S(delegate { return SceneManager.GetActiveScene().name; }));
            lines.Add("gm=" + (gm == null ? "null" : "ok") + " pl=" + (pl == null ? "null" : "ok") + " gf=" + (gf == null ? "null" : "ok"));

            if (gm != null)
            {
                lines.Add("gm.floor=" + I(delegate { return gm.floor; }, -1)
                    + " gm.gold=" + I(delegate { return gm.goldAmount; }, -1)
                    + " gm.currentHealthAmount=" + I(delegate { return gm.currentHealthAmount; }, -1)
                    + " gm.inventoryPhase=" + I(delegate { return (int)gm.inventoryPhase; }, -1)
                    + " gm.enemyEncounter='" + S(delegate { return gm.currentEnemyEncounter; }) + "'");
            }
            if (gf != null)
            {
                lines.Add("gf.battlePhase=" + I(delegate { return (int)gf.battlePhase; }, -1)
                    + " gf.turnNumber=" + I(delegate { return gf.turnNumber; }, -1)
                    + " gf.isUsingAnItem=" + B(delegate { return gf.isUsingAnItem; }, false));
            }
            if (pl != null)
            {
                lines.Add("pl.characterName=" + S(delegate { return pl.characterName.ToString(); })
                    + " pl.level=" + I(delegate { return pl.level; }, -1)
                    + " pl.AP=" + I(delegate { return pl.AP; }, -1)
                    + " pl.APperTurn=" + I(delegate { return pl.APperTurn; }, -1));
                Status st = null;
                try { st = pl.stats; } catch { }
                if (st != null)
                {
                    lines.Add("pl.stats health=" + I(delegate { return st.health; }, -1)
                        + " maxHealth=" + I(delegate { return st.maxHealth; }, -1)
                        + " armor=" + I(delegate { return st.armor; }, -1)
                        + " maxHealthBeforeItems=" + I(delegate { return st.maxHealthBeforeItems; }, -1));
                }
            }

            // Quick And Easy 到底怎么表示（SPIKE-01 §7 Q2）
            try
            {
                RunTypeManager rtm = RunTypeManager.Instance;
                if (rtm == null) lines.Add("RunTypeManager.Instance=null");
                else
                {
                    RunType rt = null;
                    try { rt = rtm.runType; } catch { }
                    if (rt == null) lines.Add("RunTypeManager.runType=null");
                    else
                    {
                        List<RunType.RunProperty> props = null;
                        try { props = rt.runProperties; } catch { }
                        lines.Add("runType.hardMode=" + B(delegate { return rt.hardMode; }, false)
                            + " key='" + S(delegate { return rt.runTypeLanguageKey; }) + "'"
                            + " props=" + (props == null ? "null" : props.Count.ToString()));
                        if (props != null)
                        {
                            for (int i = 0; i < props.Count; i++)
                            {
                                RunType.RunProperty rp = props[i];
                                if (rp == null) continue;
                                lines.Add("  runProp[" + i + "] type=" + I(delegate { return (int)rp.type; }, -1)
                                    + " value=" + I(delegate { return rp.value; }, -1));
                            }
                        }
                    }
                }
            }
            catch (Exception ex) { lines.Add("runType probe failed: " + ex.Message); }

            // 敌人：Enemy.health 和 Enemy.stats.health 到底哪个是活值
            List<Enemy> es = null;
            try { es = Enemy.allEnemies; } catch { }
            lines.Add("Enemy.allEnemies=" + (es == null ? "null" : es.Count.ToString()));
            if (es != null)
            {
                int n = 0;
                for (int i = 0; i < es.Count && n < 8; i++)
                {
                    Enemy e = es[i];
                    if (e == null) continue;
                    Status st = null;
                    try { st = e.stats; } catch { }
                    lines.Add("  enemy[" + i + "] name='" + S(delegate { return e.displayName; }) + "'"
                        + " e.health=" + I(delegate { return e.health; }, -1)
                        + " stats.health=" + (st == null ? "?" : I(delegate { return st.health; }, -1).ToString())
                        + " stats.maxHealth=" + (st == null ? "?" : I(delegate { return st.maxHealth; }, -1).ToString())
                        + " dead=" + B(delegate { return e.dead; }, false)
                        + " targetable=" + B(delegate { return e.isTargetable; }, false)
                        + " isPet=" + B(delegate { return e.isPet; }, false)
                        + " nextAttack=" + (B(delegate { return e.myNextAttack != null; }, false) ? "yes" : "no"));
                }
            }

            // 物品：allItems 里哪些算「在背包里」
            List<Item2> its = null;
            try { its = Item2.allItems; } catch { }
            lines.Add("Item2.allItems=" + (its == null ? "null" : its.Count.ToString()));
            if (its != null)
            {
                int placed = 0;
                for (int i = 0; i < its.Count; i++)
                    if (its[i] != null && IsPlaced(its[i])) placed++;
                lines.Add("  placed(gridPositions>0)=" + placed);

                int n = 0;
                for (int i = 0; i < its.Count && n < 10; i++)
                {
                    Item2 it = its[i];
                    if (it == null) continue;
                    GridObject go = null;
                    try { go = it.gridObject; } catch { }
                    int cellCount = -1;
                    if (go != null && go.gridPositions != null) cellCount = go.gridPositions.Count;
                    string text = "";
                    try
                    {
                        List<string> ds = it.descriptions;
                        if (ds != null && ds.Count > 0) text = ds[0] == null ? "" : ds[0];
                    }
                    catch { }
                    lines.Add("  item[" + i + "] '" + S(delegate { return it.displayName; }) + "'"
                        + " cost=" + I(delegate { return it.cost; }, -1)
                        + " charges=" + I(delegate { return it.currentCharges; }, -1) + "/" + I(delegate { return it.charges; }, -1)
                        + " playType=" + I(delegate { return (int)it.playType; }, -1)
                        + " cells=" + cellCount
                        + " text='" + text + "'");
                }
            }

            int stc = 0;
            try { if (Status.allStatuses != null) stc = Status.allStatuses.Count; } catch { }
            lines.Add("Status.allStatuses=" + stc);
            lines.Add("--- PROBE end ---");
            return lines;
        }
    }
}
