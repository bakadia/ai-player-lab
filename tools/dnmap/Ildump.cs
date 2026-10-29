// Dump method bodies (IL) from Assembly-CSharp.dll with Mono.Cecil.
//
// The metadata-only Dump.cs (types.json) answers "does this member exist".
// This one answers "what does it actually do" - needed when a game method
// silently ignores what we set (e.g. ItemMovement.EndDrag resetting to
// startPosition). Read-only: no game code is executed.
//
//   Ildump.exe <assembly> <TypeFullName> [method ...]
//
// With no method names it prints every method of the type. ASCII only.
using System;
using System.Collections.Generic;
using Mono.Cecil;
using Mono.Cecil.Cil;

internal static class Ildump
{
    private static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.WriteLine("usage: Ildump <assembly> <TypeFullName> [method ...]");
            return 2;
        }

        AssemblyDefinition asm = AssemblyDefinition.ReadAssembly(args[0]);
        string want = args[1];
        List<string> methods = new List<string>();
        for (int i = 2; i < args.Length; i++) methods.Add(args[i]);

        int found = 0;
        foreach (TypeDefinition t in AllTypes(asm.MainModule))
        {
            if (t.FullName != want)
            {
                // allow short name ("ItemMovement") and nested ("ItemMovement/<Move>d__85")
                if (t.Name != want) continue;
            }
            found++;
            Console.WriteLine("== " + t.FullName + " : " + t.BaseType);
            foreach (FieldDefinition f in t.Fields)
            {
                Console.WriteLine("   field " + (f.IsPublic ? "public" : "nonpublic") +
                    (f.IsStatic ? " static" : "") + " " + f.FieldType.Name + " " + f.Name);
            }
            foreach (MethodDefinition m in t.Methods)
            {
                if (methods.Count > 0 && !methods.Contains(m.Name)) continue;
                DumpMethod(m);
            }
        }

        if (found == 0)
        {
            Console.WriteLine("type not found: " + want);
            return 3;
        }
        return 0;
    }

    private static IEnumerable<TypeDefinition> AllTypes(ModuleDefinition mod)
    {
        Stack<TypeDefinition> stack = new Stack<TypeDefinition>();
        foreach (TypeDefinition t in mod.Types) stack.Push(t);
        while (stack.Count > 0)
        {
            TypeDefinition t = stack.Pop();
            yield return t;
            foreach (TypeDefinition n in t.NestedTypes) stack.Push(n);
        }
    }

    private static void DumpMethod(MethodDefinition m)
    {
        Console.WriteLine();
        Console.WriteLine("-- " + m.Name + "(" + Params(m) + ") : " + m.ReturnType.Name +
            (m.IsPublic ? " [public]" : " [nonpublic]"));
        if (!m.HasBody)
        {
            Console.WriteLine("   <no body>");
            return;
        }
        foreach (VariableDefinition v in m.Body.Variables)
        {
            Console.WriteLine("   local V_" + v.Index + " : " + v.VariableType.Name);
        }
        foreach (Instruction ins in m.Body.Instructions)
        {
            string operand = "";
            if (ins.Operand != null)
            {
                operand = ins.Operand.ToString();
            }
            Console.WriteLine("   IL_" + ins.Offset.ToString("x4") + ": " + ins.OpCode.Name + " " + operand);
        }
    }

    private static string Params(MethodDefinition m)
    {
        List<string> ps = new List<string>();
        foreach (ParameterDefinition p in m.Parameters) ps.Add(p.ParameterType.Name + " " + p.Name);
        return string.Join(", ", ps.ToArray());
    }
}
