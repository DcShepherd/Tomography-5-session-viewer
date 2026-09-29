import QtQuick

// The loading overlay's thinking orbs (plan F6.2). Python
// (`ui/widgets/loader_orbs.py`) sets the colours and the orbs' paths, and
// drives the three animators by name. They are Animators, so they advance on
// the scene graph's render thread: the orbs keep moving while the GUI thread
// is busy.
Rectangle {
    id: root
    property color surfaceColor: "#131922"
    property color toneAColor: "#6fb3a8"
    property color toneBColor: "#8997c2"
    color: surfaceColor
    // Read by Python: the .qsb is missing or unreadable.
    readonly property bool shaderFailed: orbs.status === ShaderEffect.Error
    readonly property string shaderLog: orbs.log
    // The overlay describes the load; the picture adds nothing to read.
    Accessible.ignored: true

    ShaderEffect {
        id: orbs
        objectName: "orbs"
        anchors.fill: parent
        property real time: 0
        property real lift: 0
        property real gather: 0
        property size size: Qt.size(width, height)
        property color background: root.surfaceColor
        property color toneA: root.toneAColor
        property color toneB: root.toneBColor
        property vector4d orb0x
        property vector4d orb0y
        property vector4d orb1x
        property vector4d orb1y
        property vector4d orb2x
        property vector4d orb2y
        property vector4d orb3x
        property vector4d orb3y
        fragmentShader: "orbs.frag.qsb"
    }

    // Time, in seconds, at one second per second.
    UniformAnimator {
        objectName: "clock"
        target: orbs
        uniform: "time"
        from: 0
        to: 100000
        duration: 100000000
    }

    // Extra travel at a stage change: the orbs briefly quicken.
    UniformAnimator {
        objectName: "lifter"
        target: orbs
        uniform: "lift"
        duration: 1200
        easing.type: Easing.InOutSine
    }

    // The orbs draw closer while the load links and builds.
    UniformAnimator {
        objectName: "gatherer"
        target: orbs
        uniform: "gather"
        duration: 700
        easing.type: Easing.InOutCubic
    }
}
