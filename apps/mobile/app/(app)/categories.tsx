import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { AddCategoryModal } from '@/components/AddCategoryModal';
import { CategoryDial } from '@/components/CategoryDial';
import { useCategories } from '@/stores/categoriesStore';
import { border, color, font, fontSize, radius, space } from '@/theme/tokens';

export default function CategoriesScreen() {
  const [sourceFilterAll, setSourceFilterAll] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>('recipes');
  const [showAdd, setShowAdd] = useState(false);
  const { categories, addCategory } = useCategories();

  const totalReels = categories.reduce((sum, c) => sum + c.count, 0);
  const selected = categories.find((c) => c.id === selectedId) ?? null;

  return (
    <View style={{ flex: 1, backgroundColor: color.cream, paddingHorizontal: 18, paddingTop: 54 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <Text
          style={{
            fontFamily: font.display,
            fontSize: fontSize.displayLg,
            letterSpacing: -0.6,
            color: color.ink,
            textTransform: 'uppercase',
          }}
        >
          CATEGORIES
        </Text>
        <Pressable
          onPress={() => setShowAdd(true)}
          style={{
            width: 46,
            height: 46,
            borderRadius: radius.pill,
            backgroundColor: color.ink,
            borderWidth: border.standard,
            borderColor: color.ink,
            alignItems: 'center',
            justifyContent: 'center',
            shadowColor: color.coral,
            shadowOffset: { width: 3, height: 3 },
            shadowOpacity: 1,
            shadowRadius: 0,
          }}
          accessibilityLabel="Add category"
        >
          <Text style={{ fontFamily: font.display, fontSize: 28, color: color.cream, lineHeight: 30 }}>+</Text>
        </Pressable>
      </View>

      <View style={{ height: 64, marginTop: 6, position: 'relative' }}>
        <Pressable
          onPress={() => setSourceFilterAll((v) => !v)}
          style={{
            position: 'absolute',
            top: 6,
            left: 0,
            transform: [{ rotate: sourceFilterAll ? '-3deg' : '4deg' }],
            backgroundColor: color.coral,
            borderWidth: border.standard,
            borderColor: color.ink,
            borderRadius: radius.pill,
            paddingHorizontal: space.lg,
            paddingVertical: 9,
          }}
        >
          <Text style={{ fontFamily: font.display, fontSize: 12, letterSpacing: -0.2, color: color.white }}>
            {sourceFilterAll ? 'INSTAGRAM' : 'ALL SOURCES'}
          </Text>
        </Pressable>
        <Pressable
          onPress={() => setSourceFilterAll((v) => !v)}
          style={{
            position: 'absolute',
            top: 0,
            left: 14,
            transform: [{ rotate: sourceFilterAll ? '2deg' : '-4deg' }],
            backgroundColor: color.ink,
            borderWidth: border.standard,
            borderColor: color.ink,
            borderRadius: radius.pill,
            paddingHorizontal: space.lg,
            paddingVertical: 9,
          }}
        >
          <Text style={{ fontFamily: font.display, fontSize: 12, letterSpacing: -0.2, color: color.cream }}>
            {sourceFilterAll ? 'ALL SOURCES' : 'INSTAGRAM'}
          </Text>
        </Pressable>
      </View>

      <Text
        style={{
          fontFamily: font.body,
          fontSize: fontSize.mono,
          letterSpacing: 0.6,
          color: 'rgba(21,23,15,0.55)',
          marginTop: 2,
          marginBottom: space.sm,
        }}
      >
        {totalReels} REELS · {categories.length} CATEGORIES · SCROLL THE DIAL
      </Text>

      <CategoryDial
        categories={categories}
        onSelectCategory={(id) => setSelectedId((prev) => (prev === id ? null : id))}
      />

      {selected && selected.tags.length > 0 ? (
        <View
          style={{
            position: 'absolute',
            left: 18,
            right: 18,
            bottom: 108,
            flexDirection: 'row',
            flexWrap: 'wrap',
            gap: space.sm,
          }}
        >
          {selected.tags.map((tag) => (
            <View
              key={tag}
              style={{
                backgroundColor: 'rgba(21,23,15,0.85)',
                borderRadius: radius.pill,
                paddingHorizontal: 10,
                paddingVertical: 5,
              }}
            >
              <Text style={{ fontFamily: font.body, fontSize: 10, color: color.cream }}>{tag}</Text>
            </View>
          ))}
        </View>
      ) : null}

      <AddCategoryModal
        visible={showAdd}
        onClose={() => setShowAdd(false)}
        onSubmit={addCategory}
      />
    </View>
  );
}
