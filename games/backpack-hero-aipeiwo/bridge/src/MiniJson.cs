// 极简 JSON 解析器。
//
// 为什么不用 Newtonsoft：mod 里再引一个强命名程序集，就得赌运行时能绑定到
// 和 MelonLoader / 游戏自带的那份同版本；赌输了是运行时炸。自己解析一个只吃
// 我们自己写的命令文件（Python json.dumps 产出，格式良好），风险更可控。
//
// 支持：object / array / string(含 \uXXXX) / number / true / false / null。
// 不追求：流式、大文件、错误恢复——命令文件就几百字节。

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace BphBridge
{
    internal static class MiniJson
    {
        public static object Parse(string text)
        {
            int i = 0;
            object v = ParseValue(text, ref i);
            SkipWs(text, ref i);
            return v;
        }

        // ---------- 取值的便捷方法 ----------

        public static Dictionary<string, object> Obj(object o)
        {
            return o as Dictionary<string, object>;
        }

        public static List<object> Arr(object o)
        {
            return o as List<object>;
        }

        public static string Str(Dictionary<string, object> d, string key, string fallback)
        {
            if (d == null) return fallback;
            object v;
            if (!d.TryGetValue(key, out v) || v == null) return fallback;
            return v as string ?? fallback;
        }

        public static long Num(Dictionary<string, object> d, string key, long fallback)
        {
            if (d == null) return fallback;
            object v;
            if (!d.TryGetValue(key, out v) || v == null) return fallback;
            if (v is double) return (long)(double)v;
            if (v is long) return (long)v;
            return fallback;
        }

        public static bool Bool(Dictionary<string, object> d, string key, bool fallback)
        {
            if (d == null) return fallback;
            object v;
            if (!d.TryGetValue(key, out v) || v == null) return fallback;
            if (v is bool) return (bool)v;
            return fallback;
        }

        public static Dictionary<string, object> Sub(Dictionary<string, object> d, string key)
        {
            if (d == null) return null;
            object v;
            if (!d.TryGetValue(key, out v)) return null;
            return v as Dictionary<string, object>;
        }

        // ---------- 递归下降 ----------

        private static void SkipWs(string s, ref int i)
        {
            while (i < s.Length)
            {
                char c = s[i];
                if (c == ' ' || c == '\t' || c == '\r' || c == '\n') i++;
                else break;
            }
        }

        private static object ParseValue(string s, ref int i)
        {
            SkipWs(s, ref i);
            if (i >= s.Length) throw new FormatException("unexpected end of json");

            char c = s[i];
            if (c == '{') return ParseObject(s, ref i);
            if (c == '[') return ParseArray(s, ref i);
            if (c == '"') return ParseString(s, ref i);
            if (c == 't') { Expect(s, ref i, "true"); return true; }
            if (c == 'f') { Expect(s, ref i, "false"); return false; }
            if (c == 'n') { Expect(s, ref i, "null"); return null; }
            return ParseNumber(s, ref i);
        }

        private static void Expect(string s, ref int i, string lit)
        {
            if (i + lit.Length > s.Length || s.Substring(i, lit.Length) != lit)
                throw new FormatException("expected " + lit + " at " + i);
            i += lit.Length;
        }

        private static Dictionary<string, object> ParseObject(string s, ref int i)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            i++; // {
            SkipWs(s, ref i);
            if (i < s.Length && s[i] == '}') { i++; return d; }

            while (true)
            {
                SkipWs(s, ref i);
                string key = ParseString(s, ref i);
                SkipWs(s, ref i);
                if (i >= s.Length || s[i] != ':') throw new FormatException("expected : at " + i);
                i++;
                object val = ParseValue(s, ref i);
                d[key] = val;
                SkipWs(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated object");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == '}') { i++; return d; }
                throw new FormatException("expected , or } at " + i);
            }
        }

        private static List<object> ParseArray(string s, ref int i)
        {
            List<object> a = new List<object>();
            i++; // [
            SkipWs(s, ref i);
            if (i < s.Length && s[i] == ']') { i++; return a; }

            while (true)
            {
                a.Add(ParseValue(s, ref i));
                SkipWs(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated array");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == ']') { i++; return a; }
                throw new FormatException("expected , or ] at " + i);
            }
        }

        private static string ParseString(string s, ref int i)
        {
            if (i >= s.Length || s[i] != '"') throw new FormatException("expected string at " + i);
            i++;
            StringBuilder b = new StringBuilder();
            while (true)
            {
                if (i >= s.Length) throw new FormatException("unterminated string");
                char c = s[i];
                if (c == '"') { i++; return b.ToString(); }
                if (c != '\\') { b.Append(c); i++; continue; }

                i++;
                if (i >= s.Length) throw new FormatException("bad escape");
                char e = s[i];
                i++;
                if (e == '"') b.Append('"');
                else if (e == '\\') b.Append('\\');
                else if (e == '/') b.Append('/');
                else if (e == 'b') b.Append('\b');
                else if (e == 'f') b.Append('\f');
                else if (e == 'n') b.Append('\n');
                else if (e == 'r') b.Append('\r');
                else if (e == 't') b.Append('\t');
                else if (e == 'u')
                {
                    if (i + 4 > s.Length) throw new FormatException("bad \\u escape");
                    string hex = s.Substring(i, 4);
                    i += 4;
                    b.Append((char)int.Parse(hex, NumberStyles.HexNumber, CultureInfo.InvariantCulture));
                }
                else throw new FormatException("unknown escape \\" + e);
            }
        }

        private static object ParseNumber(string s, ref int i)
        {
            int start = i;
            if (i < s.Length && (s[i] == '-' || s[i] == '+')) i++;
            while (i < s.Length)
            {
                char c = s[i];
                if ((c >= '0' && c <= '9') || c == '.' || c == 'e' || c == 'E' || c == '-' || c == '+') i++;
                else break;
            }
            string raw = s.Substring(start, i - start);
            double d;
            if (!double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out d))
                throw new FormatException("bad number: " + raw);
            return d;
        }
    }
}
