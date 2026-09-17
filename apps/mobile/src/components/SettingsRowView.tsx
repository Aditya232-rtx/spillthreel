import { Pressable, Text, View } from 'react-native';
import { border, color, font, radius, space } from '@/theme/tokens';
import type { SettingsRow } from '@/data/settings';

interface SettingsRowViewProps {
  row: SettingsRow;
  toggled?: boolean;
  onPress: () => void;
}

export function SettingsRowView({ row, toggled = false, onPress }: SettingsRowViewProps) {
  return (
    <Pressable
      onPress={onPress}
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        backgroundColor: color.cream,
        borderWidth: border.bold,
        borderColor: color.ink,
        borderRadius: radius.sm + 2,
        paddingHorizontal: space.lg,
        paddingVertical: space.md + 2,
        shadowColor: color.ink,
        shadowOffset: { width: 3, height: 3 },
        shadowOpacity: 1,
        shadowRadius: 0,
      }}
    >
      <View style={{ flex: 1 }}>
        <Text style={{ fontFamily: font.display, fontSize: 15, color: row.labelColor ?? color.ink }}>{row.label}</Text>
        {row.sub ? (
          <Text style={{ fontFamily: font.body, fontSize: 12.5, color: 'rgba(21,23,15,0.6)', marginTop: 2 }}>{row.sub}</Text>
        ) : null}
      </View>

      {row.kind === 'chevron' ? (
        <View style={{ width: 32, height: 32, borderRadius: radius.pill, backgroundColor: color.ink, alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ fontSize: 15, color: color.cream }}>↗</Text>
        </View>
      ) : null}

      {row.kind === 'chip' ? (
        <View style={{ backgroundColor: row.chipBg, borderRadius: radius.pill, paddingHorizontal: 13, paddingVertical: 6 }}>
          <Text style={{ fontFamily: font.display, fontSize: 10.5, color: row.chipText }}>{row.chipLabel}</Text>
        </View>
      ) : null}

      {row.kind === 'toggle' ? (
        <View
          style={{
            width: 44,
            height: 26,
            borderRadius: radius.pill,
            backgroundColor: toggled ? color.ink : '#CCC4B3',
            borderWidth: 1.5,
            borderColor: color.ink,
            justifyContent: 'center',
          }}
        >
          <View
            style={{
              width: 20,
              height: 20,
              borderRadius: radius.pill,
              backgroundColor: color.white,
              marginLeft: toggled ? 22 : 2,
            }}
          />
        </View>
      ) : null}
    </Pressable>
  );
}
