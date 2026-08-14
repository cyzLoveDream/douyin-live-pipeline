# BGM（本地混音）

把你自己有版权的循环配乐放到这个目录，并在 `config.yaml` 里设置：

```yaml
edit:
  bgm: assets/bgm/loop.mp3
```

流水线会用 ffmpeg `sidechaincompress` 在口播底下 duck 音量。文件不存在就跳过，不会失败。

**不要**把大 mp3 提交进 git。测试用 ffmpeg 生成很短的静音正弦波。
