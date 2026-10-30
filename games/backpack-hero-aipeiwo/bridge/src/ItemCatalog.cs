// 道具普查（item census）：把「道具总池」整表 dump 成一份 JSON 文件。
//
// 数据源：`DebugItemManager.main.item2s`（List<Item2>，全部道具 prefab 的引用），
// 必要时拼上 `ModItemLoader.main.modItems`。依据是 `ItemSpawner.GetAllValidItems()`
// 的 IL 第一条就读这张表——它就是游戏的出生池，不用解析 Unity 资产包。
//
// 只读：item2s 里是 **prefab 资产**，不在场景里。所以
//   - 可以反射读字段；
//   - 不要 GetComponentsInChildren / 不要碰 transform.parent 这类实例操作；
//   - 引用型 UnityEngine.Object 只记 `.name`（不递归进去）。
//
// 为什么直接写文件而不是塞 ack：几百件 × 十几个数组，ack JSON 会有几 MB，
// 还会拖慢 10Hz 的主循环。这里写文件，ack 只回 count / path / bytes / ms。

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using UnityEngine;

namespace BphBridge
{
    internal static class ItemCatalog
    {
        /// <summary>数据类递归深度（item -> 数组 -> 元素 -> 元素里的数组 -> ...）。</summary>
        private const int MaxDepth = 6;

        /// <summary>单个数组最多 dump 多少个元素，防止某个 256 长度的表把文件撑爆。</summary>
        private const int MaxListItems = 512;

        /// <summary>整份文档最多写多少个 JSON 值，兜底防跑飞。</summary>
        private const int MaxNodes = 400000;

        private sealed class Ctx
        {
            public int Errors;
            public int Nodes;
            public bool Truncated;
            public int MissingText;
            public readonly Dictionary<string, string> TextCache = new Dictionary<string, string>();
            public LangaugeManager Lang;
        }

        // ---------- 入口 ----------

        public static void Run(PendingCommand pc)
        {
            int t0 = Environment.TickCount;
            Ctx ctx = new Ctx();

            // 1) 道具池
            string source = "item2s";
            int coreCount = 0;
            int modCount = 0;
            List<Item2> pool = new List<Item2>();

            DebugItemManager dim = FindDebugItemManager();
            if (dim != null)
            {
                List<Item2> core = null;
                try { core = dim.item2s; } catch { ctx.Errors++; }
                if (core != null)
                {
                    coreCount = core.Count;
                    for (int i = 0; i < core.Count; i++)
                    {
                        if (core[i] != null) pool.Add(core[i]);
                    }
                }
            }
            else
            {
                // 兜底：主菜单/特殊场景里 DebugItemManager 可能还没实例化。
                // 只收「不在场景里」的 Item2（= prefab 资产），按名字去重。
                source = "scan";
                Dictionary<string, Item2> seen = new Dictionary<string, Item2>();
                try
                {
                    UnityEngine.Object[] objs = Resources.FindObjectsOfTypeAll(typeof(Item2));
                    for (int i = 0; objs != null && i < objs.Length; i++)
                    {
                        Item2 it = objs[i] as Item2;
                        if (it == null) continue;
                        bool inScene = false;
                        try { inScene = it.gameObject.scene.IsValid(); } catch { }
                        if (inScene) continue;
                        if (it.name != null && !seen.ContainsKey(it.name)) seen[it.name] = it;
                    }
                }
                catch { ctx.Errors++; }
                foreach (KeyValuePair<string, Item2> kv in seen) pool.Add(kv.Value);
                coreCount = pool.Count;
            }

            try
            {
                ModItemLoader mil = ModItemLoader.main;
                if (mil != null && mil.modItems != null)
                {
                    modCount = mil.modItems.Count;
                    for (int i = 0; i < mil.modItems.Count; i++)
                    {
                        Item2 it = mil.modItems[i];
                        if (it == null) continue;
                        if (pool.Contains(it)) continue;
                        pool.Add(it);
                    }
                }
            }
            catch { ctx.Errors++; }

            ctx.Lang = FindLangauge();

            // 2) 写文件
            string path = ResolveOutPath(MiniJson.Str(pc.Action, "out", "item-catalog.json"));

            JsonWriter w = new JsonWriter();
            w.BeginObject();
            w.Prop("action", "dump_item_catalog");
            w.Prop("game_version", SafeVersion());
            w.Prop("source", source);
            w.Prop("count", pool.Count);
            w.Prop("core_count", coreCount);
            w.Prop("mod_count", modCount);
            w.Name("items").BeginArray();
            for (int i = 0; i < pool.Count; i++)
            {
                if (ctx.Nodes >= MaxNodes) { ctx.Truncated = true; break; }
                DumpItem(w, pool[i], ctx);
            }
            w.EndArray();
            w.Prop("errors", ctx.Errors);
            w.Prop("missing_text", ctx.MissingText);
            w.Prop("truncated", ctx.Truncated);
            w.EndObject();

            string doc = w.ToString();
            byte[] bytes = new UTF8Encoding(false).GetBytes(doc);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string tmp = path + ".tmp";
            File.WriteAllBytes(tmp, bytes);
            try
            {
                if (File.Exists(path)) File.Replace(tmp, path, null);
                else File.Move(tmp, path);
            }
            catch (Exception)
            {
                File.Copy(tmp, path, true);
                try { File.Delete(tmp); } catch { }
            }

            // 3) 短 ack
            JsonWriter a = new JsonWriter();
            a.BeginObject();
            a.Prop("action", "dump_item_catalog");
            a.Prop("count", pool.Count);
            a.Prop("core_count", coreCount);
            a.Prop("mod_count", modCount);
            a.Prop("source", source);
            a.Prop("path", path);
            a.Prop("bytes", bytes.Length);
            a.Prop("errors", ctx.Errors);
            a.Prop("missing_text", ctx.MissingText);
            a.Prop("truncated", ctx.Truncated);
            a.Prop("ms", Environment.TickCount - t0);
            a.EndObject();
            pc.DiagJson = a.ToString();
        }

        // ---------- 单件道具 ----------

        /// <summary>
        /// 只 dump **数据面**：身份 / 经济 / 几何 / 效果 / 文本 / 适用。
        /// 字段名用 snake_case，是 itemdb.py 的契约；游戏原名统一进 `extra`。
        /// </summary>
        private static void DumpItem(JsonWriter w, Item2 it, Ctx ctx)
        {
            if (it == null) { w.Null(); return; }

            w.BeginObject();
            w.Prop("name", SafeName(it));
            w.Prop("display_name", Str(it, "displayName"));
            w.Prop("display_name_text", Text(ctx, Str(it, "displayName")));
            w.Prop("is_standard", Bool(it, "isStandard"));
            w.Prop("hide_rarity", Bool(it, "hideRarity"));
            w.Prop("can_be_comboed", Bool(it, "canBeComboed"));

            w.Name("item_type").Raw(EnumList(it, "itemType"));
            w.Name("item_groupings").Raw(EnumList(it, "itemGroupings"));
            w.Name("spawn_groupings").Raw(EnumList(it, "spawnGroupings"));
            w.Prop("rarity", EnumInt(it, "rarity"));
            w.Prop("one_of_a_kind", EnumInt(it, "oneOfAKindType"));
            w.Prop("play_type", EnumInt(it, "playType"));

            w.Prop("cost", Int(it, "cost"));
            w.Prop("original_cost", Int(it, "originalCost"));
            w.Prop("sigil_energy", Int(it, "sigilEnergy"));
            w.Prop("charges", Int(it, "charges"));
            w.Prop("current_charges", Int(it, "currentCharges"));
            // 注意：prefab 上 cost / originalCost / charges 大量是 -999 哨兵，
            // 真实费用在 costs[]（type: 0=energy 1=mana 2=gold）。
            w.Prop("energy_cost", CostValue(it, 0));
            w.Prop("mana_cost", CostValue(it, 1));
            w.Prop("gold_cost", CostValue(it, 2));

            w.Prop("move_area", EnumInt(it, "moveArea"));
            w.Prop("move_distance", EnumInt(it, "moveDistance"));
            w.Prop("offset_of_item_shape", Float(it, "offsetOfItemShape"));
            w.Prop("must_be_placed_on_item_type", EnumInt(it, "mustBePlacedOnItemType"));
            w.Prop("must_be_placed_on_in_combat", EnumInt(it, "mustBePlacedOnItemTypeInCombat"));

            // 占格：prefab 上 `gridObject` 字段经常是 null（它是运行时赋的），
            // 但 prefab 的子物体上确实有 GridObject / BoxCollider2D。
            // 所以先拿字段，拿不到就沿子物体找；形状按 collider 的 size 折算
            //（网格 1 格 = 1 世界单位，和 GridObject.GetGridPositions 的算法一致）。
            GridObject go = null;
            try { go = World.Field(it, "gridObject") as GridObject; } catch { ctx.Errors++; }
            if (go == null)
            {
                try
                {
                    GridObject[] gos = it.GetComponentsInChildren<GridObject>(true);
                    if (gos != null && gos.Length > 0) go = gos[0];
                }
                catch { ctx.Errors++; }
            }

            List<BoxCollider2D> colliders = FindColliders(it, go, ctx);
            int totalCells = 0;
            List<Vector2Int> shape = new List<Vector2Int>();
            w.Name("collider_shapes").BeginArray();
            for (int i = 0; i < colliders.Count; i++)
            {
                BoxCollider2D bc = colliders[i];
                if (bc == null) continue;
                Vector2 size = bc.size;
                Vector2 off = bc.offset;
                try
                {
                    Vector3 ls = bc.transform.lossyScale;
                    size = new Vector2(size.x * Mathf.Abs(ls.x), size.y * Mathf.Abs(ls.y));
                    off = new Vector2(off.x * Mathf.Abs(ls.x), off.y * Mathf.Abs(ls.y));
                }
                catch { ctx.Errors++; }

                // GridObject.GetColliderSize 的行为：朝向转 90° 时交换 x/y
                float zrot = 0f;
                try { zrot = go == null ? 0f : go.transform.rotation.eulerAngles.z; } catch { }
                if (Mathf.Abs(Mathf.Repeat(zrot, 180f) - 90f) < 1f)
                    size = new Vector2(size.y, size.x);

                int wdt = Mathf.Max(1, Mathf.RoundToInt(size.x));
                int hgt = Mathf.Max(1, Mathf.RoundToInt(size.y));
                totalCells += wdt * hgt;

                w.BeginObject();
                w.Prop("w", wdt);
                w.Prop("h", hgt);
                w.Prop("ox", off.x);
                w.Prop("oy", off.y);
                w.Prop("raw_w", size.x);
                w.Prop("raw_h", size.y);
                w.EndObject();
                ctx.Nodes++;

                int x0 = Mathf.RoundToInt(off.x + 0.5f * size.x) - wdt;
                int y0 = Mathf.RoundToInt(off.y + 0.5f * size.y) - hgt;
                for (int dx = 0; dx < wdt; dx++)
                {
                    for (int dy = 0; dy < hgt; dy++)
                    {
                        Vector2Int p = new Vector2Int(x0 + dx, y0 + dy);
                        if (!shape.Contains(p)) shape.Add(p);
                    }
                }
            }
            w.EndArray();

            w.Prop("cells", shape.Count > 0 ? shape.Count : totalCells);
            w.Name("grid_positions").BeginArray();
            for (int i = 0; i < shape.Count && i < MaxListItems; i++)
            {
                w.BeginObject();
                w.Prop("x", shape[i].x);
                w.Prop("y", shape[i].y);
                w.EndObject();
                ctx.Nodes++;
            }
            w.EndArray();
            w.Prop("grid_object_type", go == null ? -1 : (int)go.type);

            w.Name("descriptions").BeginArray();
            IList desc = World.Field(it, "descriptions") as IList;
            for (int i = 0; desc != null && i < desc.Count; i++)
            {
                string key = desc[i] as string;
                w.BeginObject();
                w.Prop("key", key == null ? "" : key);
                string text = Text(ctx, key);
                if (text == null) w.Name("text").Null();
                else w.Prop("text", text);
                w.EndObject();
                ctx.Nodes++;
            }
            w.EndArray();

            w.Name("valid_for_zones").Raw(EnumList(it, "validForZones"));
            w.Name("valid_for_characters").Raw(EnumList(it, "validForCharacters"));

            // 运行时描述：`descriptions[]` 里 833 件是空的（PITFALLS：物品描述是运行时拼的），
            // 真正的人话在 `ModTextGen.main.Generate*Description(item, effect, ...)` 里。
            // 这些字符串就是鼠标悬停时卡片上显示的那几句——**不用 OCR**。
            // 对「护甲相邻加成 / 摆放位置不同效果不同」这类信息，这是唯一的结构化来源。
            w.Name("description_runtime").BeginArray();
            List<string> rt = RuntimeDescriptions(it);
            for (int i = 0; i < rt.Count && i < 24; i++)
            {
                if (string.IsNullOrEmpty(rt[i])) continue;
                w.Value(rt[i]);
                ctx.Nodes++;
            }
            w.EndArray();

            EffectList(w, "energy_effects", it, "energyEffects", ctx);
            EffectList(w, "combat_effects", it, "combatEffects", ctx);
            EffectList(w, "movement_effects", it, "movementEffects", ctx);
            EffectList(w, "create_effects", it, "createEffects", ctx);
            EffectList(w, "modifiers", it, "modifiers", ctx);
            EffectList(w, "add_modifiers", it, "addModifiers", ctx);
            EffectList(w, "active_item_status_effects", it, "activeItemStatusEffects", ctx);
            EffectList(w, "uses_limits", it, "usesLimits", ctx);
            EffectList(w, "costs", it, "costs", ctx);

            w.Name("extra");
            DumpExtra(w, it, ctx);

            w.EndObject();
        }

        /// <summary>
        /// 用游戏自己的文案生成器拼出「悬停卡片上那几句」。
        ///
        /// `ModTextGen.Generate*Description(item, effect, reload)` 是**静态方法**，
        /// 返回已经本地化好的一句话（`reload=false` = 不要重建 UI）。
        /// 一件出错就跳过那一句：903 件的 dump 不能因为某件描述抛异常整份作废。
        /// </summary>
        public static List<string> RuntimeDescriptions(Item2 it)
        {
            List<string> outList = new List<string>();
            if (it == null) return outList;


            try
            {
                IList combat = World.Field(it, "combatEffects") as IList;
                for (int i = 0; combat != null && i < combat.Count; i++)
                {
                    string s = null;
                    try
                    {
                        s = ModTextGen.GenerateCombatEffectDescription(
                            it, combat[i] as Item2.CombattEffect, false);
                    }
                    catch { }
                    Add(outList, s);
                }
            }
            catch { }

            try
            {
                IList movement = World.Field(it, "movementEffects") as IList;
                for (int i = 0; movement != null && i < movement.Count; i++)
                {
                    string s = null;
                    try
                    {
                        s = ModTextGen.GenerateMovementEffectDescription(
                            it, movement[i] as Item2.MovementEffect, false);
                    }
                    catch { }
                    Add(outList, s);
                }
            }
            catch { }

            try
            {
                IList mods = World.Field(it, "modifiers") as IList;
                for (int i = 0; mods != null && i < mods.Count; i++)
                {
                    string s = null;
                    try
                    {
                        s = ModTextGen.GenerateModifierDescription(it, mods[i] as Item2.Modifier, false);
                    }
                    catch { }
                    Add(outList, s);
                }
            }
            catch { }

            try
            {
                IList adds = World.Field(it, "addModifiers") as IList;
                for (int i = 0; adds != null && i < adds.Count; i++)
                {
                    string s = null;
                    try
                    {
                        s = ModTextGen.GenerateAddModifierDescription(it, adds[i] as Item2.AddModifier, false);
                    }
                    catch { }
                    Add(outList, s);
                }
            }
            catch { }

            return outList;
        }

        private static void Add(List<string> list, string s)
        {
            if (string.IsNullOrEmpty(s)) return;
            s = s.Trim();
            if (s.Length == 0) return;
            if (s.IndexOf("Translation not found", StringComparison.Ordinal) >= 0) return;
            if (list.Contains(s)) return;
            list.Add(s);
        }

        /// <summary>不在契约里、但可能有用的小字段，原样给（游戏字段名）。</summary>
        private static void DumpExtra(JsonWriter w, Item2 it, Ctx ctx)
        {
            string[] covered = new string[]
            {
                "displayName", "itemType", "itemGroupings", "spawnGroupings", "rarity",
                "isStandard", "hideRarity", "canBeComboed", "oneOfAKindType", "playType",
                "cost", "originalCost", "sigilEnergy", "charges", "currentCharges",
                "moveArea", "moveDistance", "offsetOfItemShape", "mustBePlacedOnItemType",
                "mustBePlacedOnItemTypeInCombat", "gridObject", "descriptions",
                "validForZones", "validForCharacters", "energyEffects", "combatEffects",
                "movementEffects", "createEffects", "modifiers", "addModifiers",
                "activeItemStatusEffects", "usesLimits", "costs", "allItems", "name",
            };

            w.BeginObject();
            FieldInfo[] fields = it.GetType().GetFields(BindingFlags.Instance | BindingFlags.Public);
            for (int i = 0; i < fields.Length; i++)
            {
                FieldInfo f = fields[i];
                if (Array.IndexOf(covered, f.Name) >= 0) continue;
                object v;
                try { v = f.GetValue(it); }
                catch { ctx.Errors++; continue; }
                w.Name(f.Name);
                DumpValue(w, v, 1, ctx);
            }
            w.EndObject();
        }

        private static void EffectList(JsonWriter w, string key, Item2 it, string field, Ctx ctx)
        {
            w.Name(key).BeginArray();
            IList list = World.Field(it, field) as IList;
            for (int i = 0; list != null && i < list.Count && i < MaxListItems; i++)
            {
                DumpValue(w, list[i], 2, ctx);
            }
            w.EndArray();
        }

        // ---------- 通用递归 ----------

        private static void DumpValue(JsonWriter w, object v, int depth, Ctx ctx)
        {
            if (ctx.Nodes >= MaxNodes) { ctx.Truncated = true; w.Null(); return; }
            ctx.Nodes++;

            if (v == null) { w.Null(); return; }

            if (v is string) { w.Value((string)v); return; }
            if (v is bool) { w.Value((bool)v); return; }

            Type t = v.GetType();
            if (t.IsEnum) { w.Value(Convert.ToInt32(v)); return; }
            if (v is int) { w.Value((int)v); return; }
            if (v is long) { w.Value((long)v); return; }
            if (v is float) { w.Value((float)v); return; }
            if (v is double) { w.Value((float)(double)v); return; }

            if (v is Vector2)
            {
                Vector2 p = (Vector2)v;
                w.BeginObject(); w.Prop("x", p.x); w.Prop("y", p.y); w.EndObject();
                return;
            }
            if (v is Vector2Int)
            {
                Vector2Int p = (Vector2Int)v;
                w.BeginObject(); w.Prop("x", p.x); w.Prop("y", p.y); w.EndObject();
                return;
            }
            if (v is Vector3)
            {
                Vector3 p = (Vector3)v;
                w.BeginObject(); w.Prop("x", p.x); w.Prop("y", p.y); w.Prop("z", p.z); w.EndObject();
                return;
            }

            // UnityEngine.Object（prefab / 组件 / 材质）：只记名字，不递归。
            UnityEngine.Object uo = v as UnityEngine.Object;
            if (uo != null) { w.Value(SafeName(uo)); return; }

            IList list = v as IList;
            if (list != null)
            {
                w.BeginArray();
                for (int i = 0; i < list.Count && i < MaxListItems; i++)
                {
                    DumpValue(w, list[i], depth + 1, ctx);
                }
                w.EndArray();
                return;
            }

            // 游戏自己的纯数据类（Item2+Effect / Modifier / Trigger ...）：
            // 命名空间为空，且不是 UnityEngine.Object。
            if (t.Namespace == null || t.Namespace.Length == 0)
            {
                if (depth > MaxDepth) { w.Value(t.Name); return; }
                FieldInfo[] fields = t.GetFields(BindingFlags.Instance | BindingFlags.Public);
                if (fields.Length == 0) { w.Value(SafeToString(v)); return; }
                w.BeginObject();
                w.Prop("$type", t.Name);
                for (int i = 0; i < fields.Length; i++)
                {
                    object fv;
                    try { fv = fields[i].GetValue(v); }
                    catch { ctx.Errors++; continue; }
                    w.Name(fields[i].Name);
                    DumpValue(w, fv, depth + 1, ctx);
                }
                w.EndObject();
                return;
            }

            w.Value(SafeToString(v));
        }

        // ---------- 字段读取 ----------

        private static string Str(object o, string field)
        {
            object v = World.Field(o, field);
            string s = v as string;
            return s == null ? "" : s;
        }

        private static bool Bool(object o, string field)
        {
            object v = World.Field(o, field);
            return v is bool && (bool)v;
        }

        private static int Int(object o, string field)
        {
            object v = World.Field(o, field);
            if (v == null) return 0;
            try { return Convert.ToInt32(v); } catch { return 0; }
        }

        private static int EnumInt(object o, string field)
        {
            object v = World.Field(o, field);
            if (v == null) return -1;
            try { return Convert.ToInt32(v); } catch { return -1; }
        }

        private static float Float(object o, string field)
        {
            object v = World.Field(o, field);
            if (v == null) return 0f;
            try { return Convert.ToSingle(v); } catch { return 0f; }
        }

        private static string EnumList(object o, string field)
        {
            IList list = World.Field(o, field) as IList;
            if (list == null) return "[]";
            StringBuilder b = new StringBuilder("[");
            for (int i = 0; i < list.Count; i++)
            {
                if (i > 0) b.Append(',');
                try { b.Append(Convert.ToInt32(list[i])); }
                catch { b.Append("null"); }
            }
            return b.Append(']').ToString();
        }

        /// <summary>`costs[]` 里某一类费用（0=energy 1=mana 2=gold）；没有就 -1。</summary>
        private static int CostValue(Item2 it, int costType)
        {
            IList costs = World.Field(it, "costs") as IList;
            for (int i = 0; costs != null && i < costs.Count; i++)
            {
                object c = costs[i];
                object t = World.Field(c, "type");
                if (t == null || Convert.ToInt32(t) != costType) continue;
                object v = World.Field(c, "baseValue");
                if (v == null) return -1;
                try { return Convert.ToInt32(v); } catch { return -1; }
            }
            return -1;
        }

        /// <summary>
        /// prefab 上的 BoxCollider2D：优先用 GridObject 缓存的 private `colliders`，
        /// 没有再让它自己扫（`GetColliders()` 是 public），最后直接扫子物体。
        /// 这几步都只是读组件，不碰 transform.parent / 实例状态。
        /// </summary>
        private static List<BoxCollider2D> FindColliders(Item2 it, GridObject go, Ctx ctx)
        {
            List<BoxCollider2D> list = new List<BoxCollider2D>();

            if (go != null)
            {
                IList cached = World.Field(go, "colliders") as IList;
                AddColliders(list, cached);
                if (list.Count == 0)
                {
                    // GetColliders() 在 IL 里存在但没暴露成 public，走反射。
                    try { World.CallPrivate(go, "GetColliders", null); } catch { ctx.Errors++; }
                    AddColliders(list, World.Field(go, "colliders") as IList);
                }
            }

            if (list.Count == 0)
            {
                try
                {
                    BoxCollider2D[] cs = it.GetComponentsInChildren<BoxCollider2D>(true);
                    for (int i = 0; cs != null && i < cs.Length; i++)
                    {
                        if (cs[i] != null) list.Add(cs[i]);
                    }
                }
                catch { ctx.Errors++; }
            }
            return list;
        }

        private static void AddColliders(List<BoxCollider2D> list, IList src)
        {
            for (int i = 0; src != null && i < src.Count; i++)
            {
                BoxCollider2D bc = src[i] as BoxCollider2D;
                if (bc != null && !list.Contains(bc)) list.Add(bc);
            }
        }

        // ---------- 文本 ----------

        private static LangaugeManager FindLangauge()
        {
            try
            {
                UnityEngine.Object[] objs = Resources.FindObjectsOfTypeAll(typeof(LangaugeManager));
                for (int i = 0; objs != null && i < objs.Length; i++)
                {
                    LangaugeManager lm = objs[i] as LangaugeManager;
                    if (lm != null) return lm;
                }
            }
            catch { }
            return null;
        }

        /// <summary>language key -> 人话。查不到返回 null（调用方写 null）。</summary>
        private static string Text(Ctx ctx, string key)
        {
            if (key == null || key.Length == 0) return null;
            string cached;
            if (ctx.TextCache.TryGetValue(key, out cached)) return cached;
            string text = null;
            if (ctx.Lang != null)
            {
                try { text = ctx.Lang.GetTextByKey(key); }
                catch { text = null; }
            }
            if (text == null || text.Length == 0)
            {
                ctx.MissingText++;
                text = null;
            }
            ctx.TextCache[key] = text;
            return text;
        }

        // ---------- 杂项 ----------

        private static DebugItemManager FindDebugItemManager()
        {
            try
            {
                DebugItemManager dim = DebugItemManager.main;
                if (dim != null) return dim;
            }
            catch { }
            try
            {
                UnityEngine.Object[] objs = Resources.FindObjectsOfTypeAll(typeof(DebugItemManager));
                for (int i = 0; objs != null && i < objs.Length; i++)
                {
                    DebugItemManager dim = objs[i] as DebugItemManager;
                    if (dim != null) return dim;
                }
            }
            catch { }
            return null;
        }

        private static string SafeName(UnityEngine.Object o)
        {
            try { return o.name; } catch { return ""; }
        }

        private static string SafeToString(object o)
        {
            try { return o.ToString(); } catch { return ""; }
        }

        private static string SafeVersion()
        {
            try { return Application.version; } catch { return ""; }
        }

        /// <summary>
        /// `out` 相对仓库里 <game>/ 解析：默认 runtime/&lt;name&gt;。
        /// 只允许写到 runtime 的父目录内，防止误传绝对路径写坏别处。
        /// </summary>
        private static string ResolveOutPath(string outArg)
        {
            string baseDir = Path.GetFullPath(Path.GetDirectoryName(RuntimePaths.Dir));
            string raw = outArg == null ? "" : outArg.Trim();
            if (raw.Length == 0) raw = "item-catalog.json";

            string path;
            if (Path.IsPathRooted(raw)) path = Path.GetFullPath(raw);
            else if (raw.StartsWith("runtime/", StringComparison.OrdinalIgnoreCase) ||
                     raw.StartsWith("runtime\\", StringComparison.OrdinalIgnoreCase))
                path = Path.GetFullPath(Path.Combine(baseDir, raw));
            else
                path = Path.GetFullPath(Path.Combine(RuntimePaths.Dir, raw));

            if (!path.StartsWith(baseDir, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("out must stay under " + baseDir + ": " + path);
            return path;
        }
    }
}
