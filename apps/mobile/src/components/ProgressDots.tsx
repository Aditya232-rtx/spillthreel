import { View } from 'react-native';
import { border, color, radius } from '@/theme/tokens';

interface ProgressDotsProps {
  /** 1-indexed current step */
  step: 1 | 2 | 3;
  total?: number;
}

/** Three pill segments — filled coral up to current step, outlined after. */
export function ProgressDots({ step, total = 3 }: ProgressDotsProps) {
  return (
    <View style={{ flexDirection: 'row', gap: 8, alignSelf: 'flex-start' }}>
      {Array.from({ length: total }).map((_, i) => {
        const filled = i < step;
        return (
          <View
            key={i}
            style={{
              width: 22,
              height: 8,
              borderRadius: radius.pill,
              backgroundColor: filled ? color.coral : 'transparent',
              borderWidth: filled ? 0 : border.standard,
              borderColor: color.ink,
            }}
          />
        );
      })}
    </View>
  );
}
