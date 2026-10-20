import Constants from 'expo-constants';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';
import {
  hasRegisteredPushToken,
  registerPushToken,
  requestAndRegisterPush,
  unregisterOwnerPushTokens,
  type PushNotificationDriver,
  type PushRegistrationResult,
} from '@/lib/push-registration';

const nativeDriver: PushNotificationDriver = {
  getPermissionsAsync: async () => {
    const permission = await Notifications.getPermissionsAsync();
    return {
      granted: permission.granted,
      canAskAgain: permission.canAskAgain,
      status: permission.status,
    };
  },
  requestPermissionsAsync: async () => {
    const permission = await Notifications.requestPermissionsAsync();
    return {
      granted: permission.granted,
      canAskAgain: permission.canAskAgain,
      status: permission.status,
    };
  },
  getExpoPushTokenAsync: async (options) => {
    const response = await Notifications.getExpoPushTokenAsync(options);
    return { data: response.data };
  },
};

function projectId(): string | undefined {
  return Constants.easConfig?.projectId
    ?? Constants.expoConfig?.extra?.eas?.projectId;
}

export async function enableNativePush(ownerId: string): Promise<PushRegistrationResult> {
  if (Platform.OS === 'web') return { status: 'permission_unavailable' };
  return requestAndRegisterPush(nativeDriver, ownerId, { projectId: projectId() });
}

export async function disableNativePush(ownerId: string): Promise<PushRegistrationResult> {
  return unregisterOwnerPushTokens(ownerId);
}

/** Restore an opted-in account after an auth flow was cancelled or rejected. */
export async function restoreNativePush(ownerId: string): Promise<PushRegistrationResult> {
  if (Platform.OS === 'web') return { status: 'permission_unavailable' };
  return registerPushToken(nativeDriver, ownerId, { projectId: projectId() });
}

export async function nativePushEnabled(ownerId: string): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  return hasRegisteredPushToken(ownerId);
}
