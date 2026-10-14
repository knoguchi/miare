# miare

`miare`は、完成された楽曲を分析し、その曲自身の音楽的な語彙と文法を参照して
別編成へ再編曲するシステムである。

現在のMVPは、MusicXMLから主旋律、フレーズ、調性、和声候補、
`MusicalLanguage`と構造旋律を抽出し、主旋律と自律的な支持声部または対旋律からなる
2声譜を生成する。

## 実行

リポジトリ内から直接実行する場合:

```console
PYTHONPATH=src python3 -m miare analyze input.musicxml -o analysis.json

PYTHONPATH=src python3 -m miare arrange input.musicxml \
  -o arrangement.musicxml \
  --voice1-profile violin \
  --voice2-profile cello \
  --relationship support \
  --seed 1

PYTHONPATH=src python3 -m miare arrange input.musicxml \
  -o counterpoint.musicxml \
  --voice1-profile violin \
  --voice2-profile violin \
  --relationship countermelody \
  --beam-width 32 \
  --context-measures 2 \
  --seed 1

PYTHONPATH=src python3 -m miare inspect arrangement.musicxml
```

インストールする場合:

```console
python3 -m pip install -e .
miare --help
```

`arrange`はMusicXMLと、分析、生成判断、各音のprovenanceを含む
`OUTPUT.musicxml.miare.json`を出力する。生成したMusicXMLは再読込し、
パート数、小節長、音高、時刻、音価、タイが変化していないことを検証する。

歌詞、反復記号、強弱など、まだ出力へ保持できない要素を検出した場合は停止する。
内容を確認したうえで省略を許可する場合に限り、`--allow-lossy-input`を指定する。

## ブラウザで聴く

`score_viewer.html`は、編曲をブラウザ上で表示し再生する単一ファイルのビューアである。
ビルド手順も追加の依存関係もない。リポジトリ直下でHTTPサーバを起動して開く。

```console
python3 -m http.server
open http://localhost:8000/score_viewer.html
```

`samples/`の見本は一覧から1クリックで開く。左に原曲、右に編曲が並び、
1つのトランスポートが両方を同じ小節で再生する。自分のファイルは
`.musicxml`を、フレーズ境界を重ねる場合はその`.miare.json`も一緒に、
ページへドラッグ&ドロップする。

再生音はMusicXMLから合成する。録音ファイルもMIDI書き出しも不要である。
編曲出力には`<sound tempo>`が無いため、テンポはページ側で指定する。

見本は編曲器を変更したあとに作り直す。

```console
./scripts/make-samples.sh                # すべて
./scripts/make-samples.sh greensleeves   # 1つだけ
```

## 現在の範囲

- `score-partwise` MusicXML
- 単一楽章
- 調性と拍節が明瞭な曲
- 2つの単声出力
- 声部関係`support`
- beam searchによる実験的な`countermelody`
- 過去・未来の時間窓、解決待ち不協和音、既存内声を含む探索状態
- Melody、Counterpoint、Harmony、Phrase、SourceLanguage等の個別Critic
- 主題提示と回帰を保護するSourceIdentityCritic
- protected/balanced区間ごとの対旋律の自由度
- 原旋律の輪郭を保つフレーズ単位のオクターブ経路最適化
- violin、viola、cello、flute、bassoonプロファイル
- 外部生成モデルなしの決定論的生成

`dialogue`、`imitation`、`equal`、`auto`はIR上の設計には含まれるが、
まだ生成器を実装していない。指定時は別の動作へ黙って置き換えず、明示的な
エラーを返す。

## テスト

```console
PYTHONPATH=src pytest -q
```
