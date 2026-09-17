import { useState } from 'react';
import { Image, Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { AmbientDot } from '@/components/AmbientDot';
import { Badge } from '@/components/Badge';
import { PillButton } from '@/components/PillButton';
import { TextField } from '@/components/TextField';
import { color, font, fontSize, space } from '@/theme/tokens';

const LOGO_SOURCE = require('../../assets/images/logo-mark-transparent.png');

export default function LoginScreen() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  function handleContinue() {
    router.replace('/(app)/home');
  }

  return (
    <View style={{ flex: 1, backgroundColor: color.cream }}>
      <AmbientDot size={16} backgroundColor={color.coral} opacity={0.35} style={{ top: 74, left: 30 }} />
      <AmbientDot size={10} backgroundColor={color.violet} opacity={0.3} style={{ top: 150, right: 34 }} />
      <AmbientDot size={12} backgroundColor={color.gold} opacity={0.4} style={{ bottom: 120, left: 26 }} />

      <ScrollView
        contentContainerStyle={{
          flexGrow: 1,
          alignItems: 'center',
          justifyContent: 'center',
          paddingHorizontal: space.xxl,
          paddingVertical: 44,
          gap: space.md,
        }}
      >
        <Badge label="HEY AGAIN ✦" rotateDeg={-4} background={color.coral} textColor={color.white} style={{ marginBottom: space.xs }} />
        <Image source={LOGO_SOURCE} style={{ width: 66, height: 66 }} resizeMode="contain" />

        <Text
          style={{
            fontFamily: font.display,
            fontSize: fontSize.displayMd + 2,
            lineHeight: 34,
            color: color.ink,
            textTransform: 'uppercase',
            textAlign: 'center',
          }}
        >
          WELCOME BACK.
        </Text>
        <Text style={{ fontFamily: font.displayMedium, fontSize: fontSize.bodyMd, color: 'rgba(21,23,15,0.65)', textAlign: 'center' }}>
          Sign in to your library.
        </Text>

        <View style={{ width: '100%', gap: space.sm, marginTop: space.sm }}>
          <PillButton label="Continue with Google" variant="outline" minHeight={48} fontSize={14} onPress={handleContinue} />
          <PillButton label="Continue with Apple" variant="outline" minHeight={48} fontSize={14} onPress={handleContinue} />

          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, marginVertical: space.xs }}>
            <View style={{ flex: 1, height: 1.5, backgroundColor: color.ink, opacity: 0.2 }} />
            <Text style={{ fontFamily: font.body, fontSize: fontSize.mono, color: 'rgba(21,23,15,0.5)' }}>OR</Text>
            <View style={{ flex: 1, height: 1.5, backgroundColor: color.ink, opacity: 0.2 }} />
          </View>

          <TextField placeholder="you@email.com" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" />
          <TextField placeholder="password" value={password} onChangeText={setPassword} secureTextEntry />

          <Text style={{ width: '100%', textAlign: 'right', fontFamily: font.body, fontSize: 12, color: color.ink, textDecorationLine: 'underline' }}>
            Forgot password?
          </Text>

          <View style={{ marginTop: space.xs }}>
            <PillButton label="Log in" variant="coral" minHeight={50} fontSize={15} shadowColor={color.ink} onPress={handleContinue} />
          </View>
        </View>

        <Text style={{ fontFamily: font.body, fontSize: 11, color: 'rgba(21,23,15,0.6)', textAlign: 'center', marginTop: space.sm }}>
          NEW HERE?{' '}
          <Text
            onPress={() => router.push('/(auth)/signup')}
            style={{ textDecorationLine: 'underline', fontWeight: '600', color: color.ink }}
          >
            CREATE AN ACCOUNT.
          </Text>
        </Text>
      </ScrollView>
    </View>
  );
}
