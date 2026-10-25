// 手写 JSON，不依赖 Newtonsoft（避免和 MelonLoader / 游戏自带的版本打架）。
// C# 5：不能用字符串插值、?.、表达式体成员。

using System;
using System.Globalization;
using System.Text;

namespace BphBridge
{
    internal static class Json
    {
        public static string Esc(string s)
        {
            if (s == null) return "null";
            StringBuilder b = new StringBuilder("\"");
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (c == '"') b.Append("\\\"");
                else if (c == '\\') b.Append("\\\\");
                else if (c == '\n') b.Append("\\n");
                else if (c == '\r') b.Append("\\r");
                else if (c == '\t') b.Append("\\t");
                else if (c < ' ')
                    b.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                else b.Append(c);
            }
            return b.Append('"').ToString();
        }

        public static string Num(int v) { return v.ToString(CultureInfo.InvariantCulture); }
        public static string Num(long v) { return v.ToString(CultureInfo.InvariantCulture); }
        public static string Num(float v) { return v.ToString("0.###", CultureInfo.InvariantCulture); }
        public static string Bool(bool v) { return v ? "true" : "false"; }
    }

    /// <summary>
    /// 只够用的 JSON 写出器。用「是否需要逗号」一个标志位管所有层级——
    /// 前提是每层都成对写 Begin/End，且子结构写完前不动父层。
    /// </summary>
    internal sealed class JsonWriter
    {
        private readonly StringBuilder _sb = new StringBuilder(2048);
        private bool _first = true;

        private void Sep() { if (!_first) _sb.Append(','); }

        public JsonWriter BeginObject() { Sep(); _sb.Append('{'); _first = true; return this; }
        public JsonWriter EndObject() { _sb.Append('}'); _first = false; return this; }
        public JsonWriter BeginArray() { Sep(); _sb.Append('['); _first = true; return this; }
        public JsonWriter EndArray() { _sb.Append(']'); _first = false; return this; }

        public JsonWriter Name(string name)
        {
            Sep();
            _sb.Append(Json.Esc(name)).Append(':');
            _first = true;
            return this;
        }

        public JsonWriter Value(string v) { Sep(); _sb.Append(Json.Esc(v)); _first = false; return this; }
        public JsonWriter Value(int v) { Sep(); _sb.Append(Json.Num(v)); _first = false; return this; }
        public JsonWriter Value(long v) { Sep(); _sb.Append(Json.Num(v)); _first = false; return this; }
        public JsonWriter Value(float v) { Sep(); _sb.Append(Json.Num(v)); _first = false; return this; }
        public JsonWriter Value(bool v) { Sep(); _sb.Append(Json.Bool(v)); _first = false; return this; }
        public JsonWriter Null() { Sep(); _sb.Append("null"); _first = false; return this; }

        /// <summary>原样拼入一段已经合法的 JSON（不做转义）。</summary>
        public JsonWriter Raw(string json)
        {
            Sep();
            _sb.Append(json);
            _first = false;
            return this;
        }

        public JsonWriter Prop(string name, string v) { return Name(name).Value(v); }
        public JsonWriter Prop(string name, int v) { return Name(name).Value(v); }
        public JsonWriter Prop(string name, long v) { return Name(name).Value(v); }
        public JsonWriter Prop(string name, float v) { return Name(name).Value(v); }
        public JsonWriter Prop(string name, bool v) { return Name(name).Value(v); }

        public override string ToString() { return _sb.ToString(); }
    }
}
