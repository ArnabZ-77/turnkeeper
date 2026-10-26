import React, {createContext, useContext} from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';

/** Frames of transition overlap at the start of the current scene (scene content time 0 = scene start). */
export const SceneCtx = createContext<{tIn: number}>({tIn: 0});

/** Seconds since the scene's own start. Negative during the incoming transition. */
export const useSceneT = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {tIn} = useContext(SceneCtx);
  return (frame - tIn) / fps;
};

export const useTIn = () => useContext(SceneCtx).tIn;
