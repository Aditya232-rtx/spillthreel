import { useEffect } from 'react';
import { useFonts } from 'expo-font';
import { Stack } from 'expo-router';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';

SplashScreen.preventAutoHideAsync().catch(() => {
  // no-op: acceptable if called before native module is ready
});

// NOTE: Oswald-Variable.ttf is a variable font. React Native's native
// renderer (iOS/Android) does not interpolate variable-font weight axes —
// only Expo Web renders true per-weight boldness from a single variable
// file. All "weights" below currently point at the same file as a
// stopgap so nothing crashes. Before shipping, swap in static weight
// files (Oswald-Bold.ttf, Oswald-SemiBold.ttf, Oswald-Medium.ttf —
// free on Google Fonts) for correct native rendering.
const OSWALD_VARIABLE = require('../assets/fonts/Oswald-Variable.ttf');

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Oswald_700Bold: OSWALD_VARIABLE,
    Oswald_600SemiBold: OSWALD_VARIABLE,
    Oswald_500Medium: OSWALD_VARIABLE,
    MyLove: require('../assets/fonts/MyLove.otf'),
  });

  useEffect(() => {
    if (fontsLoaded || fontError) {
      SplashScreen.hideAsync().catch(() => {
        // no-op
      });
    }
  }, [fontsLoaded, fontError]);

  if (!fontsLoaded && !fontError) {
    return null;
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <StatusBar style="dark" />
        <Stack screenOptions={{ headerShown: false }} />
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
