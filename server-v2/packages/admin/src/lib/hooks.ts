import { useCallback, useEffect, useRef, useState, type DependencyList } from 'react';
import { errorMessage } from './format';

type ResourceState<T> = {
  data: T | null;
  loading: boolean;
  error: string;
};

/**
 * 极简数据加载 hook：页面进入 / 刷新 / 依赖变化时重新请求。
 * 错误不抛给调用方，交给页面渲染内联错误态。
 */
export function useResource<T>(loader: () => Promise<T>, deps: DependencyList = []) {
  const [state, setState] = useState<ResourceState<T>>({ data: null, loading: true, error: '' });
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const reload = useCallback(async (): Promise<T | null> => {
    setState((prev) => ({ ...prev, loading: true, error: '' }));
    try {
      const data = await loaderRef.current();
      setState({ data, loading: false, error: '' });
      return data;
    } catch (error) {
      setState((prev) => ({ ...prev, loading: false, error: errorMessage(error) }));
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { ...state, reload };
}
