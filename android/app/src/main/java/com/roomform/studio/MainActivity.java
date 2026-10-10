package com.roomform.studio;

import com.getcapacitor.BridgeActivity;
import android.os.Bundle;

public class MainActivity extends BridgeActivity {
    @Override public void onCreate(Bundle savedInstanceState) {
        registerPlugin(RoomScannerPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
