// Remotion CLI config (picked up from the cwd = video/).
import {Config} from '@remotion/cli/config';

Config.setPublicDir('./public');
Config.setVideoImageFormat('jpeg');
Config.setJpegQuality(92);
Config.setCodec('h264');
Config.setPixelFormat('yuv420p');
Config.setCrf(18);
Config.setOverwriteOutput(true);
// No GPU assumed: software ANGLE is the safe default on Windows.
Config.setChromiumOpenGlRenderer('swangle');
