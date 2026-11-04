#version 440
// The loading overlay's thinking orbs (plan F6.2; the F6.0 trial's look):
// soft metaballs drifting on slow, never-repeating paths, merging and
// splitting like liquid, drawn on Qt Quick's render thread so they keep moving
// while the GUI thread is busy building the interface.
//
// `ui/widgets/loader_orbs.py` passes the orbs' paths, and draws the same orbs
// with numpy when Qt Quick is not used (the fallback). The constants below are
// mirrored there, and a test checks they match. After editing this file run
// `python tools/build_loader_shader.py` to rebuild the .qsb.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float time;
    float lift;
    float gather;
    vec2 size;
    vec4 background;
    vec4 toneA;
    vec4 toneB;
    // Each orb: (x amplitude, x rate, x phase, radius) and
    // (y amplitude, y rate, y phase, share of the second tone).
    vec4 orb0x;
    vec4 orb0y;
    vec4 orb1x;
    vec4 orb1y;
    vec4 orb2x;
    vec4 orb2y;
    vec4 orb3x;
    vec4 orb3y;
};

const float BREATHE = 0.045;
const float BREATHE_RATE = 1.96;
const float GATHER_PULL = 0.35;
const float EDGE_LOW = 0.92;
const float EDGE_HIGH = 1.10;
const float TONE_B_MIX = 0.65;
const float GLOW = 0.10;
const float GLOW_LOW = 1.2;
const float GLOW_HIGH = 4.0;

// Adds one orb's field at p; `second` gathers the field in the second tone.
void orb(vec2 p, vec4 ox, vec4 oy, float t, float breathe, inout float field, inout float second) {
    float pull = 1.0 - GATHER_PULL * gather;
    vec2 c = vec2(ox.x * sin(ox.y * t + ox.z), oy.x * sin(oy.y * t + oy.z)) * pull;
    vec2 d = p - c;
    float r = ox.w * breathe;
    float f = (r * r) / max(dot(d, d), 1e-5);
    field += f;
    second += f * oy.w;
}

void main() {
    // Centred coordinates, one unit = the height, so orbs stay round.
    vec2 p = (qt_TexCoord0 - 0.5) * vec2(size.x / size.y, 1.0);
    float t = time + lift;
    float breathe = 1.0 + BREATHE * sin(t * BREATHE_RATE);
    float field = 0.0;
    float second = 0.0;
    orb(p, orb0x, orb0y, t, breathe, field, second);
    orb(p, orb1x, orb1y, t, breathe, field, second);
    orb(p, orb2x, orb2y, t, breathe, field, second);
    orb(p, orb3x, orb3y, t, breathe, field, second);
    // A soft threshold: the orbs merge and split like liquid.
    float inside = smoothstep(EDGE_LOW, EDGE_HIGH, field);
    float tone = clamp(second / max(field, 1e-5), 0.0, 1.0);
    vec3 colour = mix(toneA.rgb, toneB.rgb, tone * TONE_B_MIX);
    // A faint lift toward the dense centre of each orb.
    colour += smoothstep(GLOW_LOW, GLOW_HIGH, field) * GLOW;
    fragColor = vec4(mix(background.rgb, colour, inside), 1.0) * qt_Opacity;
}
