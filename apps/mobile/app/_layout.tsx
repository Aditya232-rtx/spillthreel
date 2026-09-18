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

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Oswald_700Bold: require('../assets/fonts/Oswald-Bold.ttf'),
    Oswald_600SemiBold: require('../assets/fonts/Oswald-SemiBold.ttf'),
    Oswald_500Medium: require('../assets/fonts/Oswald-Medium.ttf'),
    Oswald_400Regular: require('../assets/fonts/Oswald-Regular.ttf'),
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
