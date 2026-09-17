// Vue 3.5.x type shim
// Vue 3.5 removed .d.ts from the package root, but @vue/* packages are transitive deps.
import * as VueRC from '@vue/runtime-core'
import { Transition, TransitionGroup, KeepAlive, Suspense, Teleport, BaseTransition, h as vueH } from 'vue/dist/vue.runtime.esm-bundler.js'

export type { App, ComponentPublicInstance, ComputedRef, Ref, WritableComputedRef, VNode, Plugin, Directive, InjectionKey, ShallowRef, WatchHandle, ComponentOptionsBase } from '@vue/runtime-core'
export type { DeepReadonly, ShallowReadonly, ReactiveFlags, DebuggerEvent, DebuggerEventExtraInfo } from '@vue/runtime-core'

export { Transition, TransitionGroup, KeepAlive, Suspense, Teleport, BaseTransition, vueH as h }

// Re-export all VueRuntimeCore APIs
export const { createApp, createSSRApp, ref, reactive, readonly, computed, watch, watchEffect, watchPostEffect, watchSyncEffect, isRef, unref, toRef, toRefs, toReactive, toRaw, markRaw, isProxy, isReactive, isReadonly, shallowRef, shallowReactive, shallowReadonly, triggerRef, customRef, enableTracking, pauseTracking, resetTracking, effect, stop, effectScope, getCurrentScope, onScopeDispose, defineReactive, set, del, get, defineComponent, defineAsyncComponent, defineCustomElement, resolveComponent, resolveDirective, withCtx, createVNode, isVNode, cloneVNode, mergeProps, normalizeClass, normalizeStyle, mergeModels, withDirectives, withModifiers, useSlots, useAttrs, getCurrentInstance, inject, provide, nextTick, openBlock, closeBlock, pushScopeId, popScopeId, withScopeId, createCommentVNode, createElementVNode, createTextVNode, createElementBlock, createBlock, Fragment, Text, Comment, Static } = VueRC
