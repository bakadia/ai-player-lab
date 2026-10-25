using System;
using System.Collections.Generic;
using BphBridge;

internal static class TestMiniJson
{
    private static int _fail;

    private static void Check(bool ok, string label, string detail)
    {
        if (!ok) _fail++;
        Console.WriteLine((ok ? "PASS " : "FAIL ") + label + "  " + detail);
    }

    public static int Main()
    {
        // 1. 真实命令形状
        string cmd = "{\"id\":123,\"action\":{\"type\":\"use_item\",\"uid\":\"i456\"},\"timeout_ms\":5000}";
        Dictionary<string, object> d = MiniJson.Obj(MiniJson.Parse(cmd));
        Check(d != null, "root object", d == null ? "null" : "ok");
        if (d != null)
        {
            Check(MiniJson.Num(d, "id", -1) == 123, "id", MiniJson.Num(d, "id", -1).ToString());
            Check(MiniJson.Num(d, "timeout_ms", -1) == 5000, "timeout_ms", MiniJson.Num(d, "timeout_ms", -1).ToString());
            Dictionary<string, object> act = MiniJson.Sub(d, "action");
            Check(act != null, "action sub", act == null ? "null" : "ok");
            if (act != null)
            {
                Check(MiniJson.Str(act, "type", "?") == "use_item", "action.type", MiniJson.Str(act, "type", "?"));
                Check(MiniJson.Str(act, "uid", "?") == "i456", "action.uid", MiniJson.Str(act, "uid", "?"));
            }
        }

        // 2. end_turn
        d = MiniJson.Obj(MiniJson.Parse("{\"id\":7,\"action\":{\"type\":\"end_turn\"}}"));
        Dictionary<string, object> e = MiniJson.Sub(d, "action");
        Check(e != null && MiniJson.Str(e, "type", "?") == "end_turn", "end_turn", "ok");

        // 3. 嵌套数组 + 各种标量
        d = MiniJson.Obj(MiniJson.Parse("{\"a\":\"x\\ny\",\"b\":true,\"c\":null,\"d\":[1,{\"e\":2}],\"f\":-1.5e3}"));
        Check(d != null && MiniJson.Str(d, "a", "") == "x\ny", "escaped newline", MiniJson.Str(d, "a", ""));
        Check(d != null && MiniJson.Bool(d, "b", false), "bool", "ok");
        List<object> arr = d == null ? null : MiniJson.Arr(d["d"] as object);
        Check(arr != null && arr.Count == 2, "array", arr == null ? "null" : arr.Count.ToString());
        Check(d != null && MiniJson.Num(d, "f", 0) == -1500, "exponent number", MiniJson.Num(d, "f", 0).ToString());

        // 4. \uXXXX 转义
        d = MiniJson.Obj(MiniJson.Parse("{\"s\":\"\\u4e2d\\u6587\"}"));
        Check(d != null && MiniJson.Str(d, "s", "") == "\u4e2d\u6587", "unicode escape", MiniJson.Str(d, "s", ""));

        // 5. 空白容忍
        d = MiniJson.Obj(MiniJson.Parse("  {\n  \"id\" : 9 ,\t\"action\" : { \"type\" : \"noop\" } }  "));
        Check(d != null && MiniJson.Num(d, "id", -1) == 9, "whitespace", "ok");

        // 6. 缺字段要拿到 fallback，不能抛
        d = MiniJson.Obj(MiniJson.Parse("{\"id\":1}"));
        Check(d != null && MiniJson.Str(d, "nope", "def") == "def", "missing key fallback", "ok");
        Check(MiniJson.Sub(d, "action") == null, "missing sub -> null", "ok");

        // 7. 坏输入要抛（让上层能跳过坏命令）
        bool threw = false;
        try { MiniJson.Parse("{\"id\":1,"); } catch (Exception) { threw = true; }
        Check(threw, "malformed throws", "ok");

        Console.WriteLine(_fail == 0 ? "ALL PASS" : (_fail + " FAILURES"));
        return _fail;
    }
}
