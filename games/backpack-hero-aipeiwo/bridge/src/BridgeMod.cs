using MelonLoader;

[assembly: MelonInfo(typeof(BphBridge.BridgeMod), "BphBridge", "0.0.1", "ai-player-lab")]
[assembly: MelonGame("Jaspel", "Backpack Hero")]

namespace BphBridge
{
    public class BridgeMod : MelonMod
    {
        public override void OnInitializeMelon()
        {
            LoggerInstance.Msg("BphBridge loaded. game=" + UnityEngine.Application.productName);
        }

        public override void OnUpdate()
        {
            // 后面在这里轮询 runtime/cmd、写 state/ack。
        }
    }
}
