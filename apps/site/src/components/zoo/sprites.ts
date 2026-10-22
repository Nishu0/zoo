// Hand-drawn pixel sprites for the landing page.
// Each sprite is a grid of characters; every character maps to a colour in its palette.
// "." is transparent. Rows are padded to the widest row automatically.

export type Sprite = { rows: string[]; palette: Record<string, string> };

const ink = "#1d1a16";

export const sprites = {
    // the mascot from the logo: a little dark critter peeking out of its box
    keeper: {
        palette: { k: "#26221f", w: "#fbf8f2", e: ink, m: "#3a332e", p: "#f2a7b8" },
        rows: [
            ".......k........",
            "......kk........",
            "....kkkkkkk.....",
            "...kkkkkkkkkk...",
            "..kkkkkkkkkkkk..",
            "..kkwwwwwwwwkk..",
            ".kkwwwwwwwwwwkk.",
            ".kkwwewwwwewwkk.",
            ".kkwwewwwwewwkk.",
            ".kkpwwwwwwwwpkk.",
            "..kkkwwmmwwkkk..",
            "..kkkkkkkkkkkk..",
            ".kkkkkkkkkkkkkk.",
            "kkkkkkkkkkkkkkkk",
        ],
    },

    // linux desktop
    penguin: {
        palette: { k: "#22252b", w: "#fbfbf7", o: "#f3a33a", e: ink, b: "#3b4150" },
        rows: [
            "...kkkk...",
            "..kkkkkk..",
            "..kwkkwk..",
            "..kekkek..",
            "..kkookk..",
            ".kkwwwwkk.",
            "kkwwwwwwkk",
            "kbwwwwwwbk",
            "k.wwwwww.k",
            "..wwwwww..",
            "..kwwwwk..",
            "...kkkk...",
            "..oo..oo..",
        ],
    },

    // browser sandbox
    fox: {
        palette: { o: "#e8762c", O: "#c55a1a", w: "#fff6ea", k: ink, e: ink },
        rows: [
            "............o.o.",
            "............ooo.",
            "...........oooek",
            "ww.........owww.",
            "wOo..oooooooww..",
            ".Ooooooooooooo..",
            "..ooooooooooo...",
            "...wwwwwwwww....",
            "...oo.....oo....",
            "...kk.....kk....",
        ],
    },

    // code sandbox
    snake: {
        palette: { s: "#6fae4a", S: "#3f7f2c", e: ink, r: "#e0475b", y: "#f2d16b" },
        rows: [
            "............sss..",
            "...........ssses.",
            "...........ssss.rr",
            "....sSs....sS....",
            "..sSysSs..sSs....",
            ".sSs..sSs.sSy....",
            "sSs....sSsSs.....",
            "ys......sSs......",
        ],
    },

    // macOS
    giraffe: {
        palette: { y: "#ecbd52", b: "#a8642a", d: "#5e3b1f", e: ink, w: "#f7e3b0" },
        rows: [
            "..........d.d...",
            "..........y.y...",
            ".........yyyyy..",
            ".........yeyyyyw",
            ".........yyyyyww",
            ".........yyy....",
            "........yyb.....",
            "........yyy.....",
            ".......yby......",
            ".......yyy......",
            "......yyb.......",
            "yyyyyyyyy.......",
            "ybyybyyyby......",
            "yyybyyybyy......",
            ".ybyyyyyyb......",
            ".y.y...y.y......",
            ".y.y...y.y......",
            ".d.d...d.d......",
        ],
    },

    // windows
    elephant: {
        palette: { g: "#9ea7b0", G: "#77818b", w: "#fffdf5", e: ink, p: "#e9a4b4" },
        rows: [
            ".......gggggggg...",
            ".....gggggggggggg.",
            "..GGgggggggggggggg",
            ".GGGpgggggggggggggg",
            ".GGGegggggggggggggg",
            ".GGGgggggggggggggg.",
            "..GggGgggggggggggg.",
            "..gw.Ggggggggggggg.",
            "..g...ggg..gg..ggg.",
            "..g...ggg..gg..ggg.",
            ".gg...GGG..GG..GGG.",
        ],
    },

    flamingo: {
        palette: { p: "#f29bb5", P: "#d96a8e", k: ink, l: "#c9637f", w: "#fff" },
        rows: [
            "..ppp....",
            ".ppkpkk..",
            ".pp...k..",
            ".pp......",
            "..pp.....",
            "...pp....",
            "...pp....",
            ".pppppp..",
            "ppppppPP.",
            "pppppPPPp",
            ".pppppp..",
            "....l....",
            "....l....",
            "....l....",
            "...ll....",
        ],
    },

    tree: {
        palette: { g: "#5e9c4a", G: "#3f7a36", l: "#8cc06a", t: "#7a5434" },
        rows: [
            "....gggg....",
            "..gglgggGg..",
            ".gllggggGGg.",
            "gglggggGgGGg",
            "gggggGgGGGGg",
            ".ggGgGGGGGg.",
            "..gGGGGGGg..",
            "....GttG....",
            ".....tt.....",
            ".....tt.....",
            "....tttt....",
        ],
    },

    bush: {
        palette: { g: "#6aa851", G: "#4b8a3c", l: "#93c774" },
        rows: [
            "...gggg.ggg...",
            ".gglggggglgg..",
            "gglgggGgggggG.",
            "ggggGgGGgGGGGg",
            ".GGGGGGGGGGGG.",
        ],
    },

    cloud: {
        palette: { w: "#ffffff", s: "#e3eef7" },
        rows: [
            ".....wwww.......",
            "...wwwwwwww.ww..",
            ".wwwwwwwwwwwwwww",
            "wwwwwwwwwwwwwwww",
            "sssssssssssssss.",
        ],
    },

    star: {
        palette: { y: "#fff3c4" },
        rows: [".y.", "yyy", ".y."],
    },

    moon: {
        palette: { m: "#fff3c4", M: "#f0dc92" },
        rows: [
            "...mmmm...",
            ".mmmm.....",
            ".mmm......",
            "mmmM......",
            "mmmM......",
            "mmmM......",
            ".mmmM.....",
            ".mmmmM....",
            "...mmmmm..",
        ],
    },
} satisfies Record<string, Sprite>;

export type SpriteName = keyof typeof sprites;
